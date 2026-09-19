"""Read-side of the graph: search, context, impact, trace, changes, and error location.

Every function returns plain dicts. reports.py turns them into Markdown; render.py into HTML.
Rules implemented here come from .claude/prompts/risk-rubric.md and debug-playbook.md.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from collections import deque
from pathlib import Path

from .config import EXT_LANG, Paths
from . import dataflow, dynamic, gsearch
from .indexer import _ignore_patterns, index, is_indexable_path, scan_changes, sha12
from .parsers import available_languages, parse_file
from .store import Store

CONF_RANK = {"high": 3, "med": 2, "low": 1}
DEP_TYPES = ("CALLS", "REFERENCES", "EXTENDS")
LEVEL_LABEL = {1: "WILL BREAK", 2: "LIKELY AFFECTED", 3: "MAY NEED TESTING"}
MAX_LEVEL_SIZE = 40


_TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|spec)/|(^|/)test_[^/]*$|_test\.(py|go)$|\.(test|spec)\.[jt]sx?$|Tests?\.java$")


def is_test_id(symbol_or_path: str) -> bool:
    """True for symbols that live in test files. Tests are a signal to run, not code that can break."""
    return bool(_TEST_PATH.search(symbol_or_path.split("::", 1)[0]))


def _min_conf(a: str, b: str) -> str:
    return a if CONF_RANK[a] <= CONF_RANK[b] else b


def risk_level(affected: int, processes: int, is_entry: bool = False, removed_with_dependants: bool = False) -> str:
    """The table in risk-rubric.md; the highest applicable level wins."""
    levels = ["LOW"]
    if affected > 30 or processes >= 4 or is_entry:
        levels.append("CRITICAL")
    if affected >= 11 or processes >= 2 or removed_with_dependants:
        levels.append("HIGH")
    if affected >= 4 or processes >= 1:
        levels.append("MEDIUM")
    order = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    return max(levels, key=order.index)


class Atlas:
    def __init__(self, root: str | os.PathLike, shared: bool = False, data_dir: str | os.PathLike | None = None):
        self.root = Path(root).resolve()
        self.data_dir = Path(data_dir).resolve() if data_dir else None
        self.paths = Paths(self.root, self.data_dir)
        self.store = Store(self.paths.db, shared=shared)
        self._last_refresh = 0.0

    @property
    def db(self):
        return self.store.db

    # ------------------------------------------------------------------ freshness

    def refresh(self, min_interval: float = 0.0):
        """Bring the graph up to date with the working tree (a cheap no-op when nothing changed)."""
        if min_interval and time.time() - self._last_refresh < min_interval:
            return None
        result = index(self.root, data_dir=self.data_dir)
        self._last_refresh = time.time()
        return result

    def stale_files(self) -> list[str]:
        result, _, _ = scan_changes(self.root, self.store)
        return [*result.added, *result.modified, *result.removed]

    def file_is_fresh(self, path: str) -> bool:
        row = self.db.execute("SELECT sha FROM files WHERE path=?", (path,)).fetchone()
        full = self.root / path
        if not row or not full.is_file():
            return False
        return row["sha"] == sha12(full.read_bytes())

    # ------------------------------------------------------------------ lookup

    def status(self) -> dict:
        meta = self.store.meta()
        langs = self.db.execute("SELECT lang, COUNT(*) n FROM files GROUP BY lang ORDER BY n DESC").fetchall()
        broken = self.db.execute("SELECT path, error FROM files WHERE error != '' ORDER BY path").fetchall()
        return {
            "meta": meta,
            "languages": [(r["lang"], r["n"]) for r in langs],
            "parse_errors": [(r["path"], r["error"]) for r in broken],
            "clusters": [dict(r) for r in self.db.execute("SELECT * FROM clusters ORDER BY CAST(SUBSTR(id,2) AS INTEGER) LIMIT 10")],
            "processes": [dict(r) for r in self.db.execute("SELECT * FROM processes ORDER BY CAST(SUBSTR(id,2) AS INTEGER) LIMIT 10")],
            "usable_languages": sorted(available_languages()),
        }

    def find(self, query: str, limit: int = 12) -> tuple[list, str]:
        """Resolve a name to symbols. Returns (rows, how) where how is exact|fuzzy|none."""
        q = query.strip()
        db = self.db
        rows = db.execute("SELECT * FROM symbols WHERE id=?", (q,)).fetchall()
        if not rows and "::" in q:
            file_part, _, name = q.partition("::")
            rows = db.execute("SELECT * FROM symbols WHERE file LIKE ? AND (qname=? OR name=?)", (f"%{file_part}", name, name)).fetchall()
        if not rows:
            rows = db.execute("SELECT * FROM symbols WHERE kind!='module' AND (qname=? OR qname LIKE ?) ORDER BY file, start",
                              (q, f"%.{q}")).fetchall()
        if not rows:
            rows = db.execute("SELECT * FROM symbols WHERE kind!='module' AND name=? ORDER BY file, start", (q.rsplit(".", 1)[-1],)).fetchall()
            if "." in q:
                rows = [r for r in rows if r["qname"].endswith(q)] or rows
        if rows:
            return rows[:limit], "exact"
        fuzzy = [r for r in self.store.fts(q, limit) if r["kind"] != "module"]
        return fuzzy, "fuzzy" if fuzzy else "none"

    def resolve_target(self, target: str) -> tuple[list, list, str]:
        """Return (seed symbol rows, candidate rows, kind) where kind is file|symbol|ambiguous|none."""
        t = target.strip()
        norm = t.replace("\\", "/").lstrip("./")
        file_row = self.db.execute("SELECT path FROM files WHERE path=? OR path LIKE ?", (norm, f"%/{norm}")).fetchall()
        if file_row and len(file_row) == 1 and "::" not in t:
            seeds = self.db.execute("SELECT * FROM symbols WHERE file=? ORDER BY start", (file_row[0]["path"],)).fetchall()
            return seeds, [], "file"
        rows, how = self.find(t)
        if how == "exact" and len(rows) == 1:
            return rows, [], "symbol"
        if how == "exact":
            return [], rows, "ambiguous"
        return [], rows, "none" if not rows else "ambiguous"

    def symbol(self, sid: str):
        return self.db.execute("SELECT * FROM symbols WHERE id=?", (sid,)).fetchone()

    def search(self, text: str, limit: int = 15) -> list[dict]:
        out = []
        for r in self.store.fts(text, limit):
            d = dict(r)
            d["callers"] = self.db.execute("SELECT COUNT(*) FROM edges WHERE dst=? AND type IN ('CALLS','REFERENCES')", (r["id"],)).fetchone()[0]
            out.append(d)
        return out

    def graph_search(self, text: str, limit: int = 15) -> dict:
        return gsearch.graph_search(self, text, limit)

    def _graph_stamp(self) -> tuple:
        return (self.store.meta().get("lastLink"), tuple(r["sha"] for r in self.db.execute("SELECT sha FROM files WHERE lang='python' ORDER BY path")))

    def _dataflow(self, include_tests: bool) -> dict:
        key = (self._graph_stamp(), include_tests)
        cached = getattr(self, "_flow_cache", None)
        if cached and cached[0] == key:
            return cached[1]
        result = dataflow.analyse(self, include_tests)
        self._flow_cache = (key, result)
        return result

    def taint(self, target: str = "", include_tests: bool = False, limit: int = 40) -> dict:
        """Source-to-sink findings, optionally limited to a file or symbol (as a sink or as where the value entered)."""
        result = self._dataflow(include_tests)
        findings = result["findings"]
        scope = ""
        if target:
            seeds, cands, kind = self.resolve_target(target)
            if kind == "none" or (kind == "ambiguous" and not seeds):
                return {"error": kind, "candidates": [dict(r) for r in cands], "target": target}
            ids = {r["id"] for r in seeds}
            file = seeds[0]["file"] if kind == "file" and seeds else ""
            scope = file or target
            findings = [f for f in findings if (file and (f.file == file or f.function.startswith(file + "::") or f.source_function.startswith(file + "::")))
                        or f.function in ids or f.source_function in ids]
        out = []
        for f in findings[:limit]:
            callers = [dict(r) for r in self.db.execute(
                "SELECT src, conf FROM edges WHERE dst=? AND type IN ('CALLS','REFERENCES') ORDER BY conf", (f.source_function,)) if not is_test_id(r["src"])][:4]
            flows = [r["name"] for r in self.db.execute(
                "SELECT DISTINCT p.name FROM process_steps ps JOIN processes p ON p.id=ps.process_id WHERE ps.symbol_id IN (?,?) LIMIT 3", (f.function, f.source_function))]
            out.append({**f.__dict__, "callers": callers, "flows": flows})
        return {"findings": out, "total": len(findings), "files": result["files"], "functions": result["functions"],
                "skipped": result["skipped"][:10], "scope": scope}

    def defs(self, symbol: str, variable: str) -> dict:
        seeds, cands, kind = self.resolve_target(symbol)
        if kind != "symbol":
            return {"error": kind, "candidates": [dict(r) for r in cands], "target": symbol}
        return dataflow.reaching_definitions(self, seeds[0]["id"], variable)

    def unused(self, limit: int = 60) -> dict:
        return dynamic.unused(self, limit)

    # ------------------------------------------------------------------ context

    def _edges_into(self, sid: str, types=DEP_TYPES):
        marks = ",".join("?" * len(types))
        return self.db.execute(
            f"SELECT type, src, line, conf FROM edges WHERE dst=? AND type IN ({marks}) ORDER BY file, line", (sid, *types)).fetchall()

    def _edges_from(self, sid: str, types=DEP_TYPES):
        marks = ",".join("?" * len(types))
        return self.db.execute(
            f"SELECT type, dst, line, conf FROM edges WHERE src=? AND type IN ({marks}) ORDER BY line", (sid, *types)).fetchall()

    def context(self, sid: str) -> dict:
        sym = self.symbol(sid)
        callers = [dict(r) for r in self._edges_into(sid)]
        outgoing = [dict(r) for r in self._edges_from(sid)]
        resolved = [e for e in outgoing if not e["dst"].startswith("?")]
        unresolved = [e for e in outgoing if e["dst"].startswith("?")]
        children = [dict(r) for r in self.db.execute(
            "SELECT s.id, s.kind, s.start, s.end FROM edges e JOIN symbols s ON s.id=e.dst WHERE e.type='DEFINES' AND e.src=? ORDER BY s.start", (sid,))]
        imported_by = [r["src"] for r in self.db.execute("SELECT src FROM edges WHERE type='IMPORTS' AND dst=?", (sym["file"],))]
        cluster = self.db.execute("SELECT c.id, c.label FROM cluster_members m JOIN clusters c ON c.id=m.cluster_id WHERE m.symbol_id=?", (sid,)).fetchone()
        procs = [dict(r) for r in self.db.execute(
            "SELECT p.id, p.name, ps.ord FROM process_steps ps JOIN processes p ON p.id=ps.process_id WHERE ps.symbol_id=? ORDER BY p.id", (sid,))]
        return {
            "symbol": dict(sym), "callers": callers, "callees": resolved, "unresolved": unresolved, "children": children,
            "imported_by": imported_by, "cluster": dict(cluster) if cluster else None, "processes": procs,
            "source": self.source(sym, 60), "fresh": self.file_is_fresh(sym["file"]),
            "dynamic": dynamic.dynamic_use(self, sym, getattr(self, "_evidence", None)),
        }

    def source(self, sym, max_lines: int = 60) -> str:
        try:
            lines = (self.root / sym["file"]).read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return ""
        chunk = lines[sym["start"] - 1:min(sym["end"], sym["start"] - 1 + max_lines)]
        return "\n".join(f"{sym['start'] + i:>5}  {line}" for i, line in enumerate(chunk))

    # ------------------------------------------------------------------ impact

    def _walk(self, seeds: list[str], direction: str, depth: int) -> tuple[dict[int, dict[str, dict]], dict[int, int]]:
        visited = set(seeds)
        frontier = {s: "high" for s in seeds}
        levels: dict[int, dict[str, dict]] = {}
        truncated: dict[int, int] = {}
        marks = ",".join("?" * len(DEP_TYPES))
        for d in range(1, depth + 1):
            found: dict[str, dict] = {}
            for node, path_conf in frontier.items():
                if direction == "upstream":
                    rows = self.db.execute(f"SELECT src AS other, type, line, conf FROM edges WHERE dst=? AND type IN ({marks})", (node, *DEP_TYPES))
                else:
                    rows = self.db.execute(
                        f"SELECT dst AS other, type, line, conf FROM edges WHERE src=? AND type IN ({marks}) "
                        "AND dst NOT LIKE '?%' AND dst NOT LIKE 'ext:%'", (node, *DEP_TYPES))
                for r in rows:
                    other = r["other"]
                    if other in visited:
                        continue
                    conf = _min_conf(path_conf, r["conf"])
                    prev = found.get(other)
                    if not prev or CONF_RANK[conf] > CONF_RANK[prev["conf"]]:
                        found[other] = {"via": node, "type": r["type"], "line": r["line"], "conf": conf}
            if not found:
                break
            if len(found) > MAX_LEVEL_SIZE:
                truncated[d] = len(found)
                found = dict(sorted(found.items(), key=lambda kv: (-CONF_RANK[kv[1]["conf"]], kv[0]))[:MAX_LEVEL_SIZE])
            levels[d] = found
            visited |= set(found)
            frontier = {k: v["conf"] for k, v in found.items()}
            if d in truncated:
                break
        return levels, truncated

    def impact(self, target: str, direction: str = "upstream", depth: int = 3) -> dict:
        seeds_rows, candidates, kind = self.resolve_target(target)
        if kind in ("none", "ambiguous"):
            return {"error": kind, "candidates": [dict(r) for r in candidates], "target": target}
        seed_ids = [r["id"] for r in seeds_rows]
        # Changing a class also changes what its methods do, so members are seeds too.
        for r in list(seeds_rows):
            if r["kind"] == "class":
                seed_ids += [x["id"] for x in self.db.execute("SELECT id FROM symbols WHERE file=? AND qname LIKE ?", (r["file"], r["qname"] + ".%"))]
        seed_ids = list(dict.fromkeys(seed_ids))
        levels, truncated = self._walk(seed_ids, direction, min(depth, 3))
        tests = {sid: {"depth": d, **v} for d, lv in levels.items() for sid, v in lv.items() if is_test_id(sid)}
        levels = {d: {sid: v for sid, v in lv.items() if sid not in tests} for d, lv in levels.items()}
        levels = {d: lv for d, lv in levels.items() if lv}
        counted = {sid for lv in levels.values() for sid, v in lv.items() if v["conf"] != "low"}
        possible = {sid: v for lv in levels.values() for sid, v in lv.items() if v["conf"] == "low"}
        touched = {sid for lv in levels.values() for sid in lv} | set(seed_ids)
        marks = ",".join("?" * len(touched)) or "''"
        procs = [dict(r) for r in self.db.execute(
            f"SELECT DISTINCT p.id, p.name, p.entry, p.summary FROM process_steps ps JOIN processes p ON p.id=ps.process_id "
            f"WHERE ps.symbol_id IN ({marks}) ORDER BY p.id", tuple(touched)) if not r["summary"].startswith("test:")]
        is_entry = any(p["entry"] in seed_ids for p in procs)
        # Unresolved calls whose name matches a target could be dependants the graph could not link.
        names = {self.symbol(s)["name"] for s in seed_ids if self.symbol(s)["kind"] != "module"}
        maybe = []
        if direction == "upstream" and names:
            for r in self.db.execute("SELECT src, name, line FROM unresolved"):
                if r["name"].rsplit(".", 1)[-1] in names:
                    maybe.append(dict(r))
        files = sorted({r["src"] for sid in seed_ids[:1] if (row := self.symbol(sid)) for r in
                        self.db.execute("SELECT src FROM edges WHERE type='IMPORTS' AND dst=?", (row["file"],))})
        first = self.symbol(seed_ids[0])
        hidden = {}
        evidence = dynamic.Evidence(self.db)
        for sid in seed_ids[:12]:
            row = self.symbol(sid)
            found = dynamic.dynamic_use(self, row, evidence) if row else None
            if found and found["level"] in ("likely", "possible"):
                hidden[sid] = found
        return {
            "dynamic": hidden, "target": target, "kind": kind, "direction": direction, "seeds": seed_ids, "levels": levels, "truncated": truncated,
            "possible": possible, "processes": procs, "unresolved_matches": maybe, "importing_files": files, "tests": tests,
            "risk": risk_level(len(counted), len(procs), is_entry), "affected": len(counted), "is_entry": is_entry,
            "fresh": self.file_is_fresh(first["file"]) if first else True, "file": first["file"] if first else "",
            "symbols": {sid: dict(self.symbol(sid)) for sid in [*touched, *tests] if self.symbol(sid)},
        }

    # ------------------------------------------------------------------ trace

    def trace(self, source: str, dest: str) -> dict:
        a_rows, a_cand, a_kind = self.resolve_target(source)
        b_rows, b_cand, b_kind = self.resolve_target(dest)
        if a_kind != "symbol" or b_kind != "symbol":
            return {"error": "ambiguous", "from": [dict(r) for r in (a_rows or a_cand)], "to": [dict(r) for r in (b_rows or b_cand)],
                    "from_kind": a_kind, "to_kind": b_kind}
        start, goal = a_rows[0]["id"], b_rows[0]["id"]
        for allow_low in (False, True):
            path = self._bfs_path(start, goal, allow_low)
            if path:
                return {"path": path, "low": allow_low, "symbols": {i: dict(self.symbol(i)) for i, *_ in path},
                        "start": start, "goal": goal}
        reach = self._walk([start], "downstream", 3)[0]
        near = [sid for lv in reach.values() for sid in lv][:12]
        hidden = [dict(r) for r in self.db.execute("SELECT src, name, line FROM unresolved WHERE src=?", (start,))]
        return {"path": [], "start": start, "goal": goal, "reachable": near, "unresolved_from_start": hidden,
                "symbols": {i: dict(self.symbol(i)) for i in [start, goal, *near] if self.symbol(i)}}

    def _bfs_path(self, start: str, goal: str, allow_low: bool, max_depth: int = 8):
        queue = deque([(start, 0)])
        prev: dict[str, tuple[str, int, str]] = {}
        seen = {start}
        while queue:
            node, d = queue.popleft()
            if node == goal:
                path, cur = [], goal
                while cur != start:
                    parent, line, conf = prev[cur]
                    path.append((cur, line, conf))
                    cur = parent
                path.append((start, 0, "high"))
                return list(reversed(path))
            if d >= max_depth:
                continue
            for r in self.db.execute("SELECT dst, line, conf FROM edges WHERE src=? AND type IN ('CALLS','REFERENCES') "
                                     "AND dst NOT LIKE '?%' AND dst NOT LIKE 'ext:%' ORDER BY line", (node,)):
                if r["dst"] in seen or (r["conf"] == "low" and not allow_low):
                    continue
                seen.add(r["dst"])
                prev[r["dst"]] = (node, r["line"], r["conf"])
                queue.append((r["dst"], d + 1))
        return None

    # ------------------------------------------------------------------ changes

    def _git(self, *args: str) -> str | None:
        try:
            out = subprocess.run(["git", "-C", str(self.root), *args], capture_output=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            return None
        return out.stdout.decode("utf-8", "replace") if out.returncode == 0 else None

    def in_git(self) -> bool:
        return (self._git("rev-parse", "--is-inside-work-tree") or "").strip() == "true"

    def changes(self, base: str | None = None) -> dict:
        """What changed, as symbols. Baseline is a git ref (default HEAD) or, without git, the last index."""
        usable = available_languages()
        git = self.in_git() and (base is not None or self._git("rev-parse", "--verify", "HEAD") is not None)
        files: dict[str, dict] = {}   # path -> {"status", "old": {id: body_sha}, "new": {id: Symbol}}
        if git:
            ref = base or "HEAD"
            self.refresh()
            listing = self._git("diff", "--name-status", "--no-renames", ref) or ""
            for line in listing.splitlines():
                status, _, path = line.partition("\t")
                files[path.strip()] = {"status": status[:1]}
            for path in (self._git("ls-files", "--others", "--exclude-standard") or "").splitlines():
                files.setdefault(path, {"status": "A"})
            patterns = _ignore_patterns(self.root)
            for path, info in list(files.items()):
                ext = os.path.splitext(path)[1].lower()
                if EXT_LANG.get(ext) not in usable or not is_indexable_path(path, patterns):
                    del files[path]
                    continue
                lang = EXT_LANG[ext]
                old_text = self._git("show", f"{ref}:{path}") if info["status"] != "A" else None
                info["old"] = self._symbol_hashes(path, lang, old_text)
                full = self.root / path
                new_text = full.read_text(encoding="utf-8", errors="replace") if full.is_file() else None
                info["new"] = self._symbol_hashes(path, lang, new_text)
                info["status"] = "D" if new_text is None else info["status"]
            baseline = f"git {ref}"
        else:
            result, to_parse, _ = scan_changes(self.root, self.store)
            for path in [*result.added, *result.modified]:
                data, lang, _size, _mtime = to_parse[path]
                info = files.setdefault(path, {"status": "A" if path in result.added else "M"})
                info["new"] = self._symbol_hashes(path, lang, data.decode("utf-8", "replace"))
                info["old"] = {r["id"]: r["body_sha"] for r in self.db.execute("SELECT id, body_sha FROM symbols WHERE file=?", (path,))}
            for path in result.removed:
                files[path] = {"status": "D", "new": {}, "old": {r["id"]: r["body_sha"] for r in self.db.execute("SELECT id, body_sha FROM symbols WHERE file=?", (path,))}}
            baseline = f"last index ({self.store.meta().get('lastLink', 'never')})"

        added, modified, removed = [], [], []
        for path, info in sorted(files.items()):
            old, new = info.get("old", {}), info.get("new", {})
            for sid, sha in new.items():
                if sid not in old:
                    added.append(sid)
                elif old[sid] != sha:
                    modified.append(sid)
            removed += [sid for sid in old if sid not in new]
        dependants: dict[str, list[dict]] = {}
        affected: set[str] = set()
        for sid in [*modified, *removed]:
            rows = [dict(r) for r in self._edges_into(sid) if r["src"] not in set(modified) | set(removed)]
            name = sid.split("::", 1)[-1].rsplit(".", 1)[-1].split("#")[0]
            if sid in removed:
                rows += [{"type": "CALLS", "src": r["src"], "line": r["line"], "conf": "low"} for r in
                         self.db.execute("SELECT src, line FROM unresolved WHERE name=? OR name LIKE ?", (name, f"%.{name}"))]
            dependants[sid] = rows
            affected |= {r["src"] for r in rows}
        tests = sorted(a for a in affected if is_test_id(a))
        affected -= set(tests)
        touched = set(modified) | set(removed) | affected
        marks = ",".join("?" * len(touched)) or "''"
        procs = [dict(r) for r in self.db.execute(
            f"SELECT DISTINCT p.id, p.name, p.entry, p.summary FROM process_steps ps JOIN processes p ON p.id=ps.process_id "
            f"WHERE ps.symbol_id IN ({marks}) ORDER BY p.id", tuple(touched)) if not r["summary"].startswith("test:")]
        broken = any([d for d in dependants.get(s, []) if not is_test_id(d["src"])] for s in removed)
        return {
            "baseline": baseline, "git": git, "files": {p: i["status"] for p, i in files.items()}, "tests": tests,
            "added": added, "modified": modified, "removed": removed, "dependants": dependants, "processes": procs,
            "risk": risk_level(len(affected), len(procs), any(p["entry"] in modified or p["entry"] in removed for p in procs), broken),
            "affected": len(affected),
            "symbols": {sid: dict(self.symbol(sid)) for sid in touched | set(added) if self.symbol(sid)},
        }

    def _symbol_hashes(self, path: str, lang: str, text: str | None) -> dict[str, str]:
        if text is None:
            return {}
        fp = parse_file(path, lang, text, sha12(text.encode("utf-8", "replace")))
        return {s.id: s.body_sha for s in fp.symbols} if fp else {}

    # ------------------------------------------------------------------ error location

    _FRAME_PATTERNS = [
        re.compile(r'File "(?P<file>[^"]+)", line (?P<line>\d+)'),                       # Python
        re.compile(r'\bat (?:[^\s(]+ )?\(?(?P<file>[^\s():]+\.(?:js|jsx|ts|tsx|mjs|cjs)):(?P<line>\d+)'),  # Node
        re.compile(r'\((?P<file>[\w$]+\.java):(?P<line>\d+)\)'),                          # Java
        re.compile(r'(?P<file>[\w./\\-]+\.go):(?P<line>\d+)'),                            # Go
    ]

    def _match_path(self, frame_path: str) -> str | None:
        fp = frame_path.replace("\\", "/")
        best, best_len = None, 0
        for r in self.db.execute("SELECT path FROM files"):
            p = r["path"]
            if fp.endswith("/" + p) or fp == p or p.endswith("/" + fp.lstrip("./")) or p == os.path.basename(fp) and "/" not in fp:
                if len(p) > best_len:
                    best, best_len = p, len(p)
        return best

    def locate(self, text: str) -> dict:
        frames, seen = [], set()
        for pat in self._FRAME_PATTERNS:
            for m in pat.finditer(text):
                path = self._match_path(m.group("file"))
                line = int(m.group("line"))
                if path and (path, line) not in seen:
                    seen.add((path, line))
                    sym = self.db.execute("SELECT * FROM symbols WHERE file=? AND kind!='module' AND start<=? AND end>=? ORDER BY (end-start) LIMIT 1",
                                          (path, line, line)).fetchone() or self.db.execute(
                        "SELECT * FROM symbols WHERE file=? AND kind='module'", (path,)).fetchone()
                    frames.append({"file": path, "line": line, "symbol": sym["id"] if sym else None})
        if "most recent call last" in text:
            frames.reverse()   # Python prints the innermost frame last; show and focus on it first
        message = next((l.strip() for l in reversed(text.strip().splitlines())
                        if re.match(r"^[\w.]*(Error|Exception|Panic|panic|Warning)\b", l.strip()) or ": " in l), text.strip().splitlines()[-1] if text.strip() else "")
        literal = re.split(r":\s+", message, maxsplit=1)[-1] if message else ""
        literal = re.sub(r"['\"`]", "", literal)[:80]
        raise_sites = []
        if len(literal) >= 6:
            needle = literal.lower()
            for r in self.db.execute("SELECT path FROM files ORDER BY path LIMIT 800"):
                try:
                    lines = (self.root / r["path"]).read_text(encoding="utf-8", errors="replace").splitlines()
                except OSError:
                    continue
                for n, line in enumerate(lines, 1):
                    if needle in line.lower():
                        sym = self.db.execute("SELECT id FROM symbols WHERE file=? AND kind!='module' AND start<=? AND end>=? ORDER BY (end-start) LIMIT 1",
                                              (r["path"], n, n)).fetchone()
                        raise_sites.append({"file": r["path"], "line": n, "text": line.strip()[:120], "symbol": sym["id"] if sym else None})
                        if len(raise_sites) >= 8:
                            break
        hits = [dict(r) for r in self.store.fts(message or text[:200], 8) if r["kind"] != "module"] if not frames else []
        real = [f["symbol"] for f in frames if f["symbol"]]
        focus = next((x for x in real if not x.endswith("::<module>")), None) or (real[0] if real else None) \
            or next((s["symbol"] for s in raise_sites if s["symbol"]), None)
        detail = None
        if focus:
            up, _ = self._walk([focus], "upstream", 2)
            down, _ = self._walk([focus], "downstream", 1)
            detail = {"symbol": focus, "upstream": up, "downstream": down, "context": self.context(focus)}
        return {"message": message, "frames": frames, "raise_sites": raise_sites, "search_hits": hits, "focus": detail}
