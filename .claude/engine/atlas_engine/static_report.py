"""The HTML report: the interactive explorer as ONE self-contained file.

`build()` writes <root>/.claude/atlas/report/index.html. The page has the whole graph, every node's details, the source
of the files, the flows, clusters, checks and the current changes embedded as JSON, plus the explorer's own JavaScript,
CSS and drawing libraries inline. It needs no server and no network: double-click it, or let `web` serve that one file
on localhost. Search, impact and trace run in the browser (web/js/static-api.js) over the embedded edges.

Size: the data is gzip-compressed inside the file (a 3,500-file repository is a few MB). The graph is capped at
20,000 nodes and embedded source at 30 MB before compression (smaller files first). Node details are worked out in the
browser from the edges, so only signatures, summaries, unresolved calls and hidden-use evidence are stored per node.
"""

from __future__ import annotations

import base64
import gzip
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from . import dynamic
from .config import Paths
from .projects import Project
from .webapi import WebAPI

WEB_DIR = Path(__file__).resolve().parent / "web"
JS_ORDER = ["static-api", "util", "graph", "flow", "modals", "app"]
VENDOR = ["graphology.umd.min.js", "sigma.min.js", "forceatlas2.bundle.js"]
MAX_SOURCE_BYTES = 30_000_000
DEP_TYPES = ("CALLS", "REFERENCES", "EXTENDS")
MARKER = re.compile(r"<!-- atlas-report link=(\S*) generated=(\S*)")

EMPTY_CHANGES = {"baseline": "none", "git": False, "files": {}, "tests": [], "added": [], "modified": [], "removed": [], "dependants": {},
                 "processes": [], "risk": "LOW", "affected": 0, "symbols": {}}


# ------------------------------------------------------------------------------------------ JavaScript bundle


_IMPORT = re.compile(r'^import\s*\{([^}]*)\}\s*from\s*"\./([\w-]+)\.js";?[ \t]*\n', re.M)
_EXPORT = re.compile(r"^export\s+((?:async\s+)?(?:function\*?|class|const|let|var)\s+([\w$]+))", re.M)


def bundle_js() -> str:
    """The explorer's ES modules as one classic script (module scripts do not run from file://)."""
    parts: list[str] = []
    for name in JS_ORDER:
        src = (WEB_DIR / "js" / f"{name}.js").read_text(encoding="utf-8")
        head = []
        for names, dep in _IMPORT.findall(src):
            pairs = [p.strip() for p in names.split(",") if p.strip()]
            destructure = ", ".join(p.replace(" as ", ": ") for p in pairs)
            head.append(f"const {{ {destructure} }} = __m_{dep.replace('-', '_')};")
        src = _IMPORT.sub("", src)
        exported = [m[1] for m in _EXPORT.findall(src)]
        src = _EXPORT.sub(lambda m: m.group(1), src)
        parts.append(f"const __m_{name.replace('-', '_')} = (() => {{\n" + "\n".join(head) + "\n" + src + f"\nreturn {{ {', '.join(exported)} }};\n}})();")
    return "\n".join(parts)


def _safe_script(text: str) -> str:
    return text.replace("</script", "<\\/script")


# ------------------------------------------------------------------------------------------ data


def _collect(project: Project) -> dict:
    api = WebAPI(project)
    try:
        db = api.db
        api.atlas._evidence = dynamic.Evidence(db)          # shared by every node's context, instead of one load each
        status = api.status()
        status["stale"], status["stale_count"] = [], 0
        status["generated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        graph = api.graph()
        total = status["counts"]["files"] + status["counts"]["symbols"]
        overview = api.graph(overview=True) if total > 15000 else None
        ids = {n["id"] for n in graph["nodes"]}

        deps = [[r["src"], r["dst"], r["type"], r["line"], r["conf"]] for r in db.execute(
            "SELECT src, dst, type, line, conf FROM edges WHERE type IN (?,?,?) AND dst NOT LIKE '?%' AND dst NOT LIKE 'ext:%'", DEP_TYPES)
            if r["src"] in ids and r["dst"] in ids]

        summaries = {r["id"]: r["summary"] or "" for r in db.execute("SELECT id, summary FROM symbols")}
        search = []
        for n in graph["nodes"]:
            if n["l"] == "(module)":
                continue
            search.append([n["id"], n["l"], n["k"], n["f"], n.get("s", 0), summaries.get(n["id"], "")[:140]])

        # per-symbol extras the browser cannot derive from the edges
        symx: dict[str, dict] = {}
        unresolved: dict[str, list] = {}
        for r in db.execute("SELECT src, name, line FROM unresolved ORDER BY line"):
            if len(unresolved.setdefault(r["src"], [])) < 12:
                unresolved[r["src"]].append([r["name"], r["line"]])
        evidence = api.atlas._evidence
        for r in db.execute("SELECT * FROM symbols"):
            if r["id"] not in ids or r["kind"] == "module":
                continue
            extra: dict = {}
            if r["signature"]:
                extra["sig"] = r["signature"]
            if r["summary"]:
                extra["sum"] = r["summary"]
            if r["id"] in unresolved:
                extra["un"] = unresolved[r["id"]]
            found = dynamic.dynamic_use(api.atlas, r, evidence)
            if found["evidence"]:
                extra["dyn"] = found
            if extra:
                symx[r["id"]] = extra
        fx: dict[str, dict] = {}
        for r in db.execute("SELECT DISTINCT src, dst FROM edges WHERE type='IMPORTS' AND dst LIKE 'ext:%'"):
            fx.setdefault(r["src"], {}).setdefault("ext", []).append(r["dst"])
        for r in db.execute("SELECT file, name, line, reason FROM unresolved ORDER BY file, line"):
            entry = fx.setdefault(r["file"], {}).setdefault("un", [])
            if len(entry) < 30:
                entry.append([r["name"], r["line"], r["reason"]])
        for r in db.execute("SELECT path, error FROM files WHERE error != ''"):
            fx.setdefault(r["path"], {})["err"] = r["error"]

        sources: dict[str, dict] = {}
        used = 0
        files = [r["path"] for r in db.execute("SELECT path FROM files ORDER BY line_count, path")]
        for path in files:
            src = api.source(path)
            if "error" in src:
                continue
            size = sum(len(x) + 1 for x in src["lines"])
            if used + size > MAX_SOURCE_BYTES:
                continue
            used += size
            sources[path] = src

        processes = api.processes()
        try:
            changes = api.changes()
        except Exception:                     # not a git repository, or git is unavailable
            changes = dict(EMPTY_CHANGES)
        try:
            unused = api.unused()
        except Exception:
            unused = {"total": 0, "counts": {"unreferenced": 0, "possible": 0, "implicit": 0, "unassessed": 0}, "unreferenced": [], "possible": [], "implicit": [], "unassessed": []}
        try:
            taint = api.taint()
        except Exception:
            taint = {"findings": [], "total": 0, "files": 0, "functions": 0, "skipped": [], "scope": ""}

        return {
            "version": 1,
            "projects": {"projects": [{"name": project.name, "root": str(project.root), "external": False, "stats": project.stats()}], "home": project.name},
            "status": status, "graph": graph, "graphOverview": overview, "deps": deps, "search": search,
            "clusters": api.clusters(), "processes": processes,
            "processDetail": {p["id"]: api.process(p["id"]) for p in processes[:300]},
            "symx": symx, "fx": fx, "sources": sources, "changes": changes, "unused": unused, "taint": taint,
        }
    finally:
        api.close()


def _pack(data: dict) -> str:
    """JSON, gzip-compressed, base64. The browser inflates it with DecompressionStream (see static-api.js)."""
    raw = json.dumps(data, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")
    return base64.b64encode(gzip.compress(raw, 6, mtime=0)).decode("ascii")


# ------------------------------------------------------------------------------------------ the page


def render_html(project: Project) -> str:
    started = time.time()
    data = _collect(project)
    page = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    css = (WEB_DIR / "app.css").read_text(encoding="utf-8").replace("</style", "<\\/style")
    page = page.replace('<link rel="stylesheet" href="/app.css">', f"<style>\n{css}\n</style>")
    page = page.replace("<title>CodeAtlas Explorer</title>", f"<title>{_esc(project.name)} · CodeAtlas</title>")
    vendor = "\n".join(f"<script>\n{_safe_script((WEB_DIR / 'vendor' / v).read_text(encoding='utf-8'))}\n</script>" for v in VENDOR)
    scripts = (f'<script id="atlas-data" type="application/octet-stream">{_pack(data)}</script>\n{vendor}\n'
               f"<script>\n(function () {{\n{_safe_script(bundle_js())}\n}})();\n</script>")
    page = re.sub(r"<script src=\"/vendor/graphology.*?</body>", lambda m: scripts + "\n</body>", page, flags=re.S)
    version = data["status"]["meta"].get("linkCount", "0")
    marker = f"<!-- atlas-report link={version} generated={data['status']['generated']} took={time.time() - started:.1f}s -->\n"
    return page.replace("<head>", "<head>\n" + marker, 1)


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def report_path(paths: Paths) -> Path:
    return paths.app / "report" / "index.html"


def build(root: Path, data_dir: Path | None = None, name: str | None = None) -> Path:
    project = Project(name or root.resolve().name, root.resolve(), data_dir)
    out = report_path(project.paths)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(render_html(project), encoding="utf-8")
    tmp.replace(out)
    return out


def is_current(root: Path, data_dir: Path | None = None) -> bool:
    """True when the report on disk was built from the graph as it is now."""
    project = Project(root.name, root.resolve(), data_dir)
    out = report_path(project.paths)
    stats = project.stats()
    if not out.is_file() or not stats:
        return False
    with out.open("r", encoding="utf-8") as fh:
        head = fh.read(600)
    m = MARKER.search(head)
    return bool(m) and m.group(1) == str(stats["linkCount"])
