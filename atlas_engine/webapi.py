"""Data layer for the web app: everything the browser asks for, as plain JSON-safe dicts.

One WebAPI wraps one project. It reuses Atlas for the analysis (impact, trace, changes, locate) and adds what
a graph explorer needs on top: a node/edge payload, folder and file nodes, search with a small query syntax,
source viewing, flow layouts, and a read-only SQL console.
"""

from __future__ import annotations

import json
import os
import posixpath
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from .projects import Project
from .queries import Atlas

NODE_KINDS = ("folder", "file", "class", "function", "method", "module")
MAX_SOURCE_LINES = 3000
SQL_ROW_LIMIT = 500
SQL_TIME_LIMIT_S = 3.0
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

    # ------------------------------------------------------------------ search

    def search(self, query: str, limit: int = 30) -> list[dict]:
        """`name`, `path/prefix`, `type:function`, or a mix. Files and folders are searchable too."""
        q = query.strip()
        kinds = {m.lower() for m in re.findall(r"\btype:(\w+)", q, flags=re.I)}
        path_terms = re.findall(r"\bpath:(\S+)", q, flags=re.I)
        rest = re.sub(r"\b(?:type|path):\S+", " ", q, flags=re.I).strip()
        path_terms += [w for w in rest.split() if "/" in w]
        words = [w for w in rest.split() if "/" not in w]
        text = " ".join(words)
        out: dict[str, dict] = {}
        if not text and not kinds and not path_terms:
            return []

        def keep(file: str) -> bool:
            return all(p.lower() in file.lower() for p in path_terms)

        if not text and (kinds or path_terms):
            # `type:class` or `path:pkg/` with no words: list matching symbols directly.
            symbol_kinds = [k for k in kinds if k in ("class", "function", "method", "module")]
            if symbol_kinds or not kinds:
                marks = ",".join("?" * len(symbol_kinds))
                where = f"AND kind IN ({marks})" if symbol_kinds else "AND kind != 'module'"
                for r in self.db.execute(f"SELECT id, qname, kind, file, start FROM symbols WHERE 1=1 {where} ORDER BY file, start LIMIT 2000", tuple(symbol_kinds)):
                    if keep(r["file"]) and len(out) < limit * 2:
                        out[r["id"]] = {"id": r["id"], "label": r["qname"], "kind": r["kind"], "file": r["file"], "line": r["start"], "score": 0.5 if path_terms and not kinds else 1}
        if text or not path_terms:
            for r in self.atlas.store.fts(text, limit * 2) if text else []:
                kind = r["kind"] if r["kind"] != "module" else "module"
                if (not kinds or kind in kinds) and keep(r["file"]):
                    out[r["id"]] = {"id": r["id"], "label": r["qname"], "kind": kind, "file": r["file"], "line": r["start"], "score": 2}
            like = f"%{text}%"
            if text:
                for r in self.db.execute(
                        "SELECT id, qname, kind, file, start FROM symbols WHERE kind!='module' AND (qname LIKE ? OR name LIKE ?) LIMIT ?", (like, like, limit * 2)):
                    if (not kinds or r["kind"] in kinds) and keep(r["file"]) and r["id"] not in out:
                        out[r["id"]] = {"id": r["id"], "label": r["qname"], "kind": r["kind"], "file": r["file"], "line": r["start"], "score": 1}
        if not kinds or "file" in kinds:
            needle = text.lower()
            for r in self.db.execute("SELECT path FROM files ORDER BY path"):
                p = r["path"]
                if keep(p) and (not needle or needle in posixpath.basename(p).lower() or needle in p.lower()):
                    out.setdefault(p, {"id": p, "label": posixpath.basename(p), "kind": "file", "file": p, "line": 1, "score": 3 if needle and needle in posixpath.basename(p).lower() else 1})
        if not kinds or "folder" in kinds:
            dirs = {posixpath.dirname(r["path"]) for r in self.db.execute("SELECT path FROM files")}
            all_dirs = {d for full in dirs if full for d in ["/".join(full.split("/")[:i]) for i in range(1, len(full.split("/")) + 1)]}
            needle = text.lower()
            for d in sorted(all_dirs):
                if keep(d) and (not needle or needle in d.lower()):
                    out.setdefault(f"dir:{d}", {"id": f"dir:{d}", "label": posixpath.basename(d), "kind": "folder", "file": d, "line": 0, "score": 2 if not text else 1})
        ranked = sorted(out.values(), key=lambda r: (-r["score"], len(r["label"]), r["label"]))
        if text:
            low = text.lower()
            ranked.sort(key=lambda r: (0 if r["label"].lower() == low else 1 if r["label"].lower().startswith(low) else 2, -r["score"], len(r["label"])))
        return ranked[:limit]

    # ------------------------------------------------------------------ node detail

    def node(self, node_id: str) -> dict:
        if node_id.startswith("dir:"):
            d = node_id[4:]
            rows = list(self.db.execute("SELECT path, lang, line_count FROM files WHERE path LIKE ? ORDER BY path", (f"{d}/%",)))
            direct = [r for r in rows if posixpath.dirname(r["path"]) == d]
            sub = sorted({r["path"][len(d) + 1:].split("/")[0] for r in rows if "/" in r["path"][len(d) + 1:]})
            return {"type": "folder", "id": node_id, "path": d, "files": [r["path"] for r in direct], "folders": sub,
                    "total_files": len(rows), "lines": sum(r["line_count"] or 0 for r in rows)}
        f = self.db.execute("SELECT * FROM files WHERE path=?", (node_id,)).fetchone()
        if f:
            syms = [_row(r) for r in self.db.execute("SELECT id, kind, qname, start, end, summary FROM symbols WHERE file=? ORDER BY start", (node_id,))]
            imports = [r["dst"] for r in self.db.execute("SELECT DISTINCT dst FROM edges WHERE type='IMPORTS' AND src=? ORDER BY dst", (node_id,))]
            imported_by = [r["src"] for r in self.db.execute("SELECT DISTINCT src FROM edges WHERE type='IMPORTS' AND dst=? ORDER BY src", (node_id,))]
            unresolved = [_row(r) for r in self.db.execute("SELECT src, name, line, reason FROM unresolved WHERE file=? ORDER BY line LIMIT 30", (node_id,))]
            return {"type": "file", "id": node_id, "path": node_id, "lang": f["lang"], "lines": f["line_count"], "error": f["error"],
                    "sha": f["sha"], "indexed_at": f["indexed_at"], "symbols": syms, "imports": imports, "imported_by": imported_by, "unresolved": unresolved}
        sym = self.atlas.symbol(node_id)
        if not sym:
            return {"type": "missing", "id": node_id}
        c = self.atlas.context(node_id)
        c["type"] = "symbol"
        c["id"] = node_id
        c["impact_counts"] = {"callers": len(c["callers"]), "callees": len(c["callees"])}
        return c

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

    # ------------------------------------------------------------------ read-only SQL

    def sql(self, query: str) -> dict:
        """Run a SELECT against the graph database. Read-only at the SQLite level, time and row limited."""
        q = query.strip().rstrip(";")
        if not q:
            return {"error": "Empty query"}
        if not re.match(r"^(select|with|explain)\b", q, flags=re.I):
            return {"error": "Only SELECT queries are allowed here."}
        conn = sqlite3.connect(f"file:{self.project.paths.db}?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, getattr(sqlite3, "SQLITE_RECURSIVE", 33)}
        conn.set_authorizer(lambda action, *_: sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY)
        import time
        deadline = time.time() + SQL_TIME_LIMIT_S
        conn.set_progress_handler(lambda: 1 if time.time() > deadline else 0, 20000)
        try:
            cur = conn.execute(q)
            cols = [d[0] for d in cur.description or []]
            rows = [list(r) for r in cur.fetchmany(SQL_ROW_LIMIT + 1)]
        except sqlite3.Error as exc:
            return {"error": str(exc)}
        finally:
            conn.close()
        return {"columns": cols, "rows": rows[:SQL_ROW_LIMIT], "truncated": len(rows) > SQL_ROW_LIMIT}

    def schema(self) -> dict:
        out = {}
        for t in self.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name NOT LIKE 'symbols_fts%' ORDER BY name"):
            out[t["name"]] = [c["name"] for c in self.db.execute(f"PRAGMA table_info({t['name']})")]
        return out

    # ------------------------------------------------------------------ analysis passthroughs

    def impact(self, target: str, direction: str = "upstream") -> dict:
        r = self.atlas.impact(target, direction if direction in ("upstream", "downstream") else "upstream")
        if "error" in r:
            return r
        r["levels"] = {str(d): lv for d, lv in r["levels"].items()}
        r["truncated"] = {str(d): n for d, n in r["truncated"].items()}
        return r

    def trace(self, src: str, dst: str) -> dict:
        r = self.atlas.trace(src, dst)
        if "path" in r:
            r["path"] = [list(p) for p in r["path"]]
        return r

    def changes(self, base: str | None = None) -> dict:
        r = self.atlas.changes(base or None)
        r["dependants"] = {k: v for k, v in r["dependants"].items()}
        return r

    def locate(self, text: str) -> dict:
        r = self.atlas.locate(text)
        if r.get("focus"):
            f = r["focus"]
            f["upstream"] = {str(d): lv for d, lv in f["upstream"].items()}
            f["downstream"] = {str(d): lv for d, lv in f["downstream"].items()}
            f.pop("context", None)
        return r

    def overview(self) -> str:
        """A compact Markdown summary the chat model can read first."""
        from . import reports
        return reports.md_status(self.atlas.status())
