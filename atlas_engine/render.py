"""Static HTML pages for the CodeAtlas app. Spec: .claude/prompts/html-generation-rules.md.

Pages open straight from disk. The only scripts are the Mermaid loader and a small filter box on the
Symbols page. Every graph-derived value is HTML-escaped.
"""

from __future__ import annotations

import html
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from .config import Paths
from .reports import short
from .store import Store

NAV = [("index.html", "Overview"), ("symbols.html", "Symbols"), ("flows.html", "Flows"), ("clusters.html", "Clusters"),
       ("impact.html", "Impact"), ("debug.html", "Debug"), ("changes.html", "Changes")]
MERMAID = """<script type="module">
  import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs";
  mermaid.initialize({ startOnLoad: true, theme: "neutral" });
</script>"""
FILTER_JS = """<script>
  const box = document.getElementById("q");
  box.addEventListener("input", () => {
    const needle = box.value.trim().toLowerCase();
    document.querySelectorAll("details.file").forEach(d => {
      let any = false;
      d.querySelectorAll("tbody tr").forEach(tr => {
        const hit = !needle || tr.textContent.toLowerCase().includes(needle);
        tr.hidden = !hit; any = any || hit;
      });
      d.hidden = !any; if (needle && any) d.open = true;
    });
  });
</script>"""
REPORT_KINDS = {"impact": "Impact reports", "debug": "Debug reports", "changes": "Change reports"}
EMPTY_HINT = {
    "index": "The graph has not been built yet. Run <code>codeatlas index</code> (or <code>/atlas-index</code> in Claude Code).",
}


def e(value: object) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _shell(title: str, current: str, body: str, meta: dict[str, str], *, sub: bool = False,
           diagrams: bool = False, extra_script: str = "") -> str:
    up = "../" if sub else ""
    current_attr = ' aria-current="page"'
    nav = "".join(f'<a href="{up}{href}"{current_attr if href == current else ""}>{label}</a>' for href, label in NAV)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)} — CodeAtlas</title>
<link rel="stylesheet" href="{up}assets/style.css">
</head>
<body>
<header>
  <span class="brand">CodeAtlas</span>
  <nav>{nav}</nav>
</header>
<main>
{body}
</main>
<footer>Generated {stamp} · last full index {e(meta.get('lastFullIndex', 'never'))} · clustering: {e(meta.get('clusterAlgo', 'n/a'))}</footer>
{MERMAID if diagrams else ""}
{extra_script}
</body>
</html>
"""


def _table(headers: list[str], rows: list[list[str]], raw_cols: set[int] | None = None) -> str:
    raw_cols = raw_cols or set()
    head = "".join(f"<th>{e(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c if i in raw_cols else e(c)}</td>" for i, c in enumerate(r)) + "</tr>" for r in rows)
    return f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def _diagram(src: str) -> str:
    return f'<pre class="mermaid">{e(src)}</pre>'


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if path.read_text(encoding="utf-8") == text:
            return
    except OSError:
        pass
    path.write_text(text, encoding="utf-8")


# ------------------------------------------------------------------------------------ pages


def publish_all(store: Store, paths: Paths) -> None:
    meta = store.meta()
    _write(paths.app / "index.html", _overview(store, paths, meta))
    _write(paths.app / "symbols.html", _symbols(store, meta))
    _write(paths.app / "flows.html", _flows(store, meta))
    _write(paths.app / "clusters.html", _clusters(store, meta))
    for kind in REPORT_KINDS:
        _write(paths.app / f"{kind}.html", _report_list(paths, kind, meta))


def _overview(store: Store, paths: Paths, meta: dict[str, str]) -> str:
    db = store.db
    if not int(meta.get("fileCount", "0") or 0):
        return _shell("Overview", "index.html", f'<h1>Overview</h1><p class="empty">{EMPTY_HINT["index"]}</p>', meta)
    stats = [("Files", meta.get("fileCount")), ("Symbols", meta.get("symbolCount")), ("Edges", meta.get("edgeCount")),
             ("Unresolved", meta.get("unresolvedCount")), ("Clusters", db.execute("SELECT COUNT(*) FROM clusters").fetchone()[0]),
             ("Flows", db.execute("SELECT COUNT(*) FROM processes").fetchone()[0])]
    cards = "".join(f'<div class="stat"><b>{e(v)}</b><span>{e(k)}</span></div>' for k, v in stats)
    langs = ", ".join(f"{e(r['lang'])} {r['n']}" for r in db.execute("SELECT lang, COUNT(*) n FROM files GROUP BY lang ORDER BY n DESC"))
    body = [f"<h1>Overview</h1>", f'<div class="stats">{cards}</div>', f'<p class="muted">Languages: {langs}</p>']
    errors = db.execute("SELECT path, error FROM files WHERE error != '' ORDER BY path").fetchall()
    if errors:
        body.append("<h2>Files with parse errors</h2><p class=\"muted\">Symbols were recovered by a tolerant scan; these files have no call edges until the syntax is fixed.</p>")
        body.append(_table(["file", "error"], [[r["path"], r["error"]] for r in errors[:15]]))
    clusters = db.execute("SELECT * FROM clusters ORDER BY CAST(SUBSTR(id,2) AS INTEGER) LIMIT 8").fetchall()
    if clusters:
        body.append('<h2>Top clusters</h2>' + _table(["id", "label", "symbols", "cohesion", "summary"],
                    [[c["id"], c["label"], c["size"], c["cohesion"], c["summary"]] for c in clusters]) + '<p><a href="clusters.html">All clusters →</a></p>')
    procs = db.execute("SELECT * FROM processes ORDER BY CAST(SUBSTR(id,2) AS INTEGER) LIMIT 8").fetchall()
    if procs:
        body.append('<h2>Top flows</h2>' + _table(["id", "flow", "steps", "summary"], [[p["id"], p["name"], p["steps"], p["summary"]] for p in procs])
                    + '<p><a href="flows.html">All flows →</a></p>')
    for kind, label in REPORT_KINDS.items():
        latest = _report_entries(paths, kind)[:3]
        if latest:
            items = "".join(f'<li><a href="reports/{e(f)}">{e(t)}</a> <span class="muted">{e(d)}</span></li>' for f, t, d, _ in latest)
            body.append(f'<h2>{label}</h2><ul class="reports">{items}</ul>')
    return _shell("Overview", "index.html", "\n".join(body), meta)


def _symbols(store: Store, meta: dict[str, str]) -> str:
    db = store.db
    rows = db.execute("SELECT * FROM symbols WHERE kind != 'module' ORDER BY file, start").fetchall()
    if not rows:
        return _shell("Symbols", "symbols.html", '<h1>Symbols</h1><p class="empty">No symbols yet. Run <code>codeatlas index</code>.</p>', meta)
    callers = Counter({r["dst"]: r["n"] for r in db.execute(
        "SELECT dst, COUNT(*) n FROM edges WHERE type IN ('CALLS','REFERENCES') AND dst NOT LIKE '?%' GROUP BY dst")})
    callees = Counter({r["src"]: r["n"] for r in db.execute(
        "SELECT src, COUNT(*) n FROM edges WHERE type IN ('CALLS','REFERENCES') AND dst NOT LIKE '?%' GROUP BY src")})
    by_file = defaultdict(list)
    for r in rows:
        by_file[r["file"]].append(r)
    blocks = []
    for file, syms in by_file.items():
        table = _table(["Symbol", "Kind", "Lines", "Summary", "Callers", "Callees"],
                       [[s["qname"], s["kind"], f"{s['start']}–{s['end']}", s["summary"] or s["signature"][:80], callers[s["id"]], callees[s["id"]]] for s in syms])
        blocks.append(f'<details class="file"{" open" if len(by_file) <= 5 else ""}><summary>{e(file)} <span class="muted">({len(syms)})</span></summary>{table}</details>')
    body = ('<h1>Symbols</h1><p class="muted">Filter by name, kind, file or summary.</p>'
            '<p><input id="q" type="search" placeholder="Filter symbols…" aria-label="Filter symbols" style="width:100%;max-width:420px;padding:8px 10px"></p>'
            + "\n".join(blocks))
    return _shell("Symbols", "symbols.html", body, meta, extra_script=FILTER_JS)


def _flows(store: Store, meta: dict[str, str]) -> str:
    db = store.db
    procs = db.execute("SELECT * FROM processes ORDER BY CAST(SUBSTR(id,2) AS INTEGER)").fetchall()
    if not procs:
        return _shell("Flows", "flows.html", '<h1>Execution flows</h1><p class="empty">No flows detected yet. Flows need an entry point that reaches at least two other symbols.</p>', meta)
    cards = []
    for p in procs:
        steps = [r["symbol_id"] for r in db.execute("SELECT symbol_id FROM process_steps WHERE process_id=? ORDER BY ord", (p["id"],))]
        marks = ",".join("?" * len(steps))
        edges = [(r["src"], r["dst"], r["conf"]) for r in db.execute(
            f"SELECT src, dst, conf FROM edges WHERE type='CALLS' AND src IN ({marks}) AND dst IN ({marks})", (*steps, *steps))]
        from .reports import mermaid
        cards.append(f'<div class="card"><h3>{e(p["id"])} · {e(p["name"])}</h3><p class="muted">{e(p["summary"])}</p>'
                     f'{_diagram(mermaid(edges, {steps[0]}))}<p class="muted">Entry: {e(short(p["entry"]))}</p></div>')
    return _shell("Flows", "flows.html", f'<h1>Execution flows</h1><div class="cards">{"".join(cards)}</div>', meta, diagrams=True)


def _clusters(store: Store, meta: dict[str, str]) -> str:
    db = store.db
    clusters = db.execute("SELECT * FROM clusters ORDER BY CAST(SUBSTR(id,2) AS INTEGER)").fetchall()
    if not clusters:
        return _shell("Clusters", "clusters.html", '<h1>Clusters</h1><p class="empty">No clusters yet. Run <code>codeatlas index</code>.</p>', meta)
    owner = {r["symbol_id"]: r["cluster_id"] for r in db.execute("SELECT * FROM cluster_members")}
    links: Counter = Counter()
    for r in db.execute("SELECT src, dst FROM edges WHERE type IN ('CALLS','REFERENCES','EXTENDS') AND dst NOT LIKE '?%' AND dst NOT LIKE 'ext:%'"):
        a, b = owner.get(r["src"]), owner.get(r["dst"])
        if a and b and a != b:
            links[(a, b)] += 1
    label = {c["id"]: c["label"] for c in clusters}
    lines = ["flowchart TB"] + ['  %s["%s"]' % (c["id"], label[c["id"]].replace('"', "'")) for c in clusters[:25]]
    lines += [f"  {a} -->|{n}| {b}" for (a, b), n in links.most_common(15) if a in label and b in label]
    overview = _diagram("\n".join(lines)) if links else ""
    cards = []
    for c in clusters:
        members = [r["symbol_id"] for r in db.execute("SELECT symbol_id FROM cluster_members WHERE cluster_id=? ORDER BY symbol_id", (c["id"],))]
        shown = "".join('<li><code>%s</code> <span class="muted">%s</span></li>' % (e(short(m)), e(m.split("::")[0])) for m in members[:12])
        more = f'<li class="muted">+{len(members) - 12} more</li>' if len(members) > 12 else ""
        tone = {"high": "low", "med": "medium"}.get(c["cohesion"], "high")   # badge colours: green = cohesive
        cards.append(f'<div class="card"><h3>{e(c["id"])} · {e(c["label"])}</h3><p><span class="badge {tone}">'
                     f'cohesion {e(c["cohesion"])}</span> <span class="muted">{c["size"]} symbols</span></p><ul>{shown}{more}</ul></div>')
    body = f'<h1>Clusters</h1><p class="muted">Functional areas found by community detection ({e(meta.get("clusterAlgo", ""))}).</p>{overview}<div class="cards">{"".join(cards)}</div>'
    return _shell("Clusters", "clusters.html", body, meta, diagrams=bool(overview))


# ------------------------------------------------------------------------------------ reports


def _report_entries(paths: Paths, kind: str) -> list[tuple[str, str, str, str]]:
    out = []
    for f in paths.reports.glob(f"{kind}-*.html"):
        m = re.search(r"(\d{8})-(\d{4})\.html$", f.name)
        if not m:
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        title = re.search(r"<title>(.*?) — CodeAtlas</title>", text)
        badge = re.search(r'class="badge (\w+)"', text)
        stamp = f"{m.group(1)[:4]}-{m.group(1)[4:6]}-{m.group(1)[6:]} {m.group(2)[:2]}:{m.group(2)[2:]}"
        out.append((f.name, html.unescape(title.group(1)) if title else f.stem, stamp, badge.group(1) if badge else ""))
    return sorted(out, key=lambda x: x[2], reverse=True)[:50]


def _report_list(paths: Paths, kind: str, meta: dict[str, str]) -> str:
    entries = _report_entries(paths, kind)
    label = REPORT_KINDS[kind]
    command = {"impact": "codeatlas impact &lt;symbol&gt; --save", "debug": "codeatlas locate --save", "changes": "codeatlas changes --save"}[kind]
    if not entries:
        return _shell(label, f"{kind}.html", f'<h1>{label}</h1><p class="empty">No reports yet. Run <code>{command}</code> (or the matching <code>/atlas-*</code> command).</p>', meta)
    def badge_html(b: str) -> str:
        return f' <span class="badge {b}">{b.upper()}</span>' if b else ""

    items = "".join(
        f'<li><a href="reports/{e(f)}">{e(t)}</a>{badge_html(b)} <span class="muted">{e(d)}</span></li>'
        for f, t, d, b in entries)
    return _shell(label, f"{kind}.html", f'<h1>{label}</h1><ul class="reports">{items}</ul>', meta)


def _inline(text: str) -> str:
    text = e(text)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    return re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)


def md_to_html(md: str) -> tuple[str, bool]:
    """Convert the small Markdown subset that reports.py emits. Returns (html, has_diagram)."""
    out: list[str] = []
    lines = md.splitlines()
    i, diagram, in_list = 0, False, False

    def close_list():
        nonlocal in_list
        if in_list:
            out.append("</ul>")
            in_list = False

    while i < len(lines):
        line = lines[i]
        if line.startswith("```"):
            close_list()
            lang, block = line[3:].strip(), []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                block.append(lines[i])
                i += 1
            if lang == "mermaid":
                out.append(_diagram("\n".join(block)))
                diagram = True
            else:
                out.append(f"<pre><code>{e(chr(10).join(block))}</code></pre>")
        elif line.startswith("|") and i + 1 < len(lines) and lines[i + 1].startswith("|--"):
            close_list()
            headers = [c.strip() for c in line.strip("|").split("|")]
            i += 2
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split(" | ")])
                i += 1
            out.append(_table(headers, rows))
            continue
        elif re.match(r"^#{1,3} ", line):
            close_list()
            level = len(line) - len(line.lstrip("#"))
            out.append(f"<h{level}>{_inline(line[level + 1:])}</h{level}>")
        elif line.startswith("> "):
            close_list()
            out.append(f'<p class="empty"><strong>{_inline(line[2:].replace("**", ""))}</strong></p>')
        elif line.startswith("- "):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{_inline(line[2:])}</li>")
        elif line.strip():
            close_list()
            out.append(f"<p>{_inline(line)}</p>")
        else:
            close_list()
        i += 1
    close_list()
    return "\n".join(out), diagram


def write_report(paths: Paths, store: Store, kind: str, title: str, markdown: str) -> Path:
    """Save one analysis as reports/<kind>-<slug>-<timestamp>.html and refresh the matching list page."""
    meta = store.meta()
    body, has_diagram = md_to_html(markdown)
    badge = re.search(r"[Rr]isk:? \*\*(LOW|MEDIUM|HIGH|CRITICAL)\*\*", markdown)
    if badge:
        body = f'<p><span class="badge {badge.group(1).lower()}">{badge.group(1)}</span></p>\n' + body
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    slug = (slug[len(kind) + 1:] if slug.startswith(kind + "-") else slug)[:40] or kind
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    path = paths.reports / f"{kind}-{slug}-{stamp}.html"
    _write(path, _shell(title, f"{kind}.html", body, meta, sub=True, diagrams=has_diagram))
    _write(paths.app / f"{kind}.html", _report_list(paths, kind, meta))
    return path
