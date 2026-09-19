"""Data layer for the HTML report: everything the explorer shows, as plain JSON-safe dicts.

One WebAPI wraps one project. It reuses Atlas for the analysis and adds what a graph explorer needs on top: a
node/edge payload, folder and file nodes, node details, source viewing and flow layouts. static_report.py calls it
to fill the report; the browser never talks to it.
"""

from __future__ import annotations

import json
import posixpath
import re
from collections import Counter, defaultdict

from .projects import Project
from .queries import Atlas

NODE_KINDS = ("folder", "file", "class", "function", "method", "module")
MAX_SOURCE_LINES = 3000
GRAPH_NODE_LIMIT = 20000


def _row(r) -> dict:
    return {k: r[k] for k in r.keys()}


class WebAPI:
    def __init__(self, project: Project):
        self.project = project
        self.atlas = Atlas(project.root, shared=True, data_dir=project.data_dir)

    def close(self) -> None:
        self.atlas.store.close()

    @property
    def db(self):
        return self.atlas.db

    # ------------------------------------------------------------------ status and graph

    def status(self) -> dict:
        s = self.atlas.status()
        meta = s["meta"]
        stale = []
        try:
            stale = self.atlas.stale_files()
        except Exception:
            pass
        return {
            "name": self.project.name, "root": str(self.project.root), "meta": meta,
            "languages": s["languages"], "parse_errors": s["parse_errors"], "usable_languages": s["usable_languages"],
            "skipped": self._skipped(meta),
            "stale": stale[:50], "stale_count": len(stale),
            "counts": {k: self.db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                       for k, t in (("files", "files"), ("symbols", "symbols"), ("clusters", "clusters"), ("processes", "processes"))},
        }

    @staticmethod
    def _skipped(meta: dict) -> list:
        try:
            return json.loads(meta.get("skipped", "[]"))
        except ValueError:
            return []

    def graph(self, limit: int = GRAPH_NODE_LIMIT, overview: bool = False) -> dict:
        """Nodes and edges for the canvas. Folder and file nodes are added so the tree structure is visible.

        `overview=True` returns only folders and files plus IMPORTS/CONTAINS, for repositories too large to draw whole.
        """
        db = self.db
        files = list(db.execute("SELECT path, lang, line_count, error FROM files ORDER BY path"))
        symbols = [] if overview else list(db.execute("SELECT id, file, kind, name, qname, start, end FROM symbols"))
        total = len(files) + len(symbols)
        cluster_of = {r["symbol_id"]: r["cluster_id"] for r in db.execute("SELECT symbol_id, cluster_id FROM cluster_members")}
        nodes: dict[str, dict] = {}
        folders: set[str] = set()
        for f in files:
            parts = f["path"].split("/")
            for i in range(1, len(parts)):
                folders.add("/".join(parts[:i]))
        for d in sorted(folders):
            nodes[f"dir:{d}"] = {"id": f"dir:{d}", "l": posixpath.basename(d), "k": "folder", "f": d}
        for f in files:
            nodes[f["path"]] = {"id": f["path"], "l": posixpath.basename(f["path"]), "k": "file", "f": f["path"],
                                "n": f["line_count"], "err": 1 if f["error"] else 0, "lang": f["lang"]}
        for s in symbols:
            kind = s["kind"] if s["kind"] in NODE_KINDS else "function"
            label = "(module)" if s["kind"] == "module" else s["qname"]
            n = {"id": s["id"], "l": label, "k": kind, "f": s["file"], "s": s["start"], "e": s["end"]}
            if s["id"] in cluster_of:
                n["c"] = cluster_of[s["id"]]
            nodes[s["id"]] = n

        edges: list[list] = []
        seen: set[tuple] = set()

        def add(src: str, dst: str, etype: str, conf: str = "high") -> None:
            key = (src, dst, etype)
            if src in nodes and dst in nodes and src != dst and key not in seen:
                seen.add(key)
                edges.append([src, dst, etype, conf])

        for d in folders:
            parent = posixpath.dirname(d)
            add(f"dir:{parent}" if parent else "", f"dir:{d}", "CONTAINS")
        for f in files:
            parent = posixpath.dirname(f["path"])
            if parent:
                add(f"dir:{parent}", f["path"], "CONTAINS")
        types = ("IMPORTS",) if overview else ("CALLS", "REFERENCES", "EXTENDS", "IMPORTS", "DEFINES")
        marks = ",".join("?" * len(types))
        for r in db.execute(f"SELECT type, src, dst, conf FROM edges WHERE type IN ({marks}) AND dst NOT LIKE '?%' AND dst NOT LIKE 'ext:%'", types):
            add(r["src"], r["dst"], r["type"], r["conf"])

        degree: Counter = Counter()
        for s, d, *_ in edges:
            degree[s] += 1
            degree[d] += 1
        for nid, n in nodes.items():
            n["d"] = degree.get(nid, 0)
        listing = list(nodes.values())
        truncated = False
        if len(listing) > limit:
            keep = {n["id"] for n in sorted(listing, key=lambda n: -n["d"])[:limit]}
            listing = [n for n in listing if n["id"] in keep]
            edges = [e for e in edges if e[0] in keep and e[1] in keep]
            truncated = True
        return {"nodes": listing, "edges": edges, "total_nodes": total + len(folders), "truncated": truncated, "overview": overview}


    def unused(self) -> dict:
        return self.atlas.unused(80)

    def taint(self, target: str = "", tests: bool = False) -> dict:
        return self.atlas.taint(target, include_tests=tests, limit=60)



    def source(self, file: str, start: int | None = None, end: int | None = None) -> dict:
        row = self.db.execute("SELECT path, lang FROM files WHERE path=?", (file,)).fetchone()
        if not row:
            return {"error": "unknown file"}
        root = self.project.root.resolve()
        full = (root / file).resolve()
        if root not in full.parents and full != root:      # never serve anything outside the project
            return {"error": "outside project"}
        try:
            lines = full.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            return {"error": f"unreadable: {exc.strerror}"}
        total = len(lines)
        return {"file": file, "lang": row["lang"], "total": total, "truncated": total > MAX_SOURCE_LINES,
                "lines": lines[:MAX_SOURCE_LINES], "start": start, "end": end}

    # ------------------------------------------------------------------ clusters and flows

    def clusters(self) -> list[dict]:
        out = []
        for c in self.db.execute("SELECT * FROM clusters ORDER BY CAST(SUBSTR(id,2) AS INTEGER)"):
            members = [r["symbol_id"] for r in self.db.execute("SELECT symbol_id FROM cluster_members WHERE cluster_id=? ORDER BY symbol_id", (c["id"],))]
            files = sorted({m.split("::", 1)[0] for m in members})
            out.append({**_row(c), "members": members, "files": files})
        return out

    def processes(self) -> list[dict]:
        cluster_of = {r["symbol_id"]: r["cluster_id"] for r in self.db.execute("SELECT symbol_id, cluster_id FROM cluster_members")}
        out = []
        for p in self.db.execute("SELECT * FROM processes ORDER BY CAST(SUBSTR(id,2) AS INTEGER)"):
            steps = [r["symbol_id"] for r in self.db.execute("SELECT symbol_id FROM process_steps WHERE process_id=? ORDER BY ord", (p["id"],))]
            spans = {cluster_of.get(s) for s in steps if s in cluster_of}
            out.append({**_row(p), "steps_ids": steps, "clusters": len(spans), "cross": len(spans) > 1, "is_test": p["summary"].startswith("test:")})
        return out

    def process(self, pid: str) -> dict:
        p = next((x for x in self.processes() if x["id"] == pid), None)
        if not p:
            return {"error": "unknown process"}
        steps = p["steps_ids"]
        marks = ",".join("?" * len(steps)) or "''"
        edges = [{"src": r["src"], "dst": r["dst"], "conf": r["conf"]} for r in self.db.execute(
            f"SELECT src, dst, conf FROM edges WHERE type='CALLS' AND src IN ({marks}) AND dst IN ({marks})", (*steps, *steps))]
        syms = {r["id"]: _row(r) for r in self.db.execute(f"SELECT id, kind, qname, file, start FROM symbols WHERE id IN ({marks})", tuple(steps))}
        depth = {steps[0]: 0} if steps else {}
        children = defaultdict(list)
        for e in edges:
            children[e["src"]].append(e["dst"])
        queue = [steps[0]] if steps else []
        while queue:
            cur = queue.pop(0)
            for nxt in children.get(cur, []):
                if nxt not in depth:
                    depth[nxt] = depth[cur] + 1
                    queue.append(nxt)
        return {**p, "edges": edges, "symbols": syms, "depth": depth, "mermaid": self.mermaid(steps, edges, syms)}

    @staticmethod
    def mermaid(steps: list[str], edges: list[dict], syms: dict) -> str:
        ids = {s: f"n{i + 1}" for i, s in enumerate(steps[:60])}
        lines = ["flowchart TD"]
        for s, nid in ids.items():
            label = (syms.get(s, {}).get("qname") or s.split("::")[-1]).replace('"', "'")
            lines.append(f'  {nid}["{label}"]')
        for e in edges:
            if e["src"] in ids and e["dst"] in ids:
                lines.append(f"  {ids[e['src']]} {'-.->' if e['conf'] == 'low' else '-->'} {ids[e['dst']]}")
        return "\n".join(lines)

    # ------------------------------------------------------------------ analysis passthroughs


    def changes(self, base: str | None = None) -> dict:
        r = self.atlas.changes(base or None)
        r["dependants"] = {k: v for k, v in r["dependants"].items()}
        return r
