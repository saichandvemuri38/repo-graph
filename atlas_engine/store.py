"""SQLite store: files, symbols, raw + resolved edges, clusters, processes, and full-text search."""

from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator

from .model import FileParse, ImportBinding, RawEdge, Symbol
from .resolve import Bindings, Resolved, Unresolved

DB_VERSION = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS files(
  path TEXT PRIMARY KEY, lang TEXT, sha TEXT, size INTEGER, mtime_ns INTEGER,
  line_count INTEGER, indexed_at TEXT, error TEXT);
CREATE TABLE IF NOT EXISTS symbols(
  id TEXT PRIMARY KEY, file TEXT, kind TEXT, name TEXT, qname TEXT,
  start INTEGER, end INTEGER, signature TEXT, summary TEXT, body_sha TEXT);
CREATE INDEX IF NOT EXISTS symbols_file ON symbols(file);
CREATE INDEX IF NOT EXISTS symbols_name ON symbols(name);
CREATE TABLE IF NOT EXISTS raw_edges(
  file TEXT, type TEXT, src TEXT, target TEXT, line INTEGER, level INTEGER, aux TEXT,
  inferred INTEGER, local INTEGER, ctx TEXT);
CREATE INDEX IF NOT EXISTS raw_edges_file ON raw_edges(file);
CREATE TABLE IF NOT EXISTS imports(file TEXT, alias TEXT, module TEXT, name TEXT, level INTEGER, line INTEGER);
CREATE INDEX IF NOT EXISTS imports_file ON imports(file);
CREATE TABLE IF NOT EXISTS edges(type TEXT, src TEXT, dst TEXT, line INTEGER, conf TEXT, file TEXT);
CREATE INDEX IF NOT EXISTS edges_src ON edges(src, type);
CREATE INDEX IF NOT EXISTS edges_dst ON edges(dst, type);
CREATE INDEX IF NOT EXISTS edges_file ON edges(file);
CREATE TABLE IF NOT EXISTS unresolved(src TEXT, name TEXT, line INTEGER, candidates TEXT, reason TEXT, file TEXT);
CREATE TABLE IF NOT EXISTS clusters(id TEXT PRIMARY KEY, label TEXT, size INTEGER, cohesion TEXT, summary TEXT);
CREATE TABLE IF NOT EXISTS cluster_members(cluster_id TEXT, symbol_id TEXT);
CREATE INDEX IF NOT EXISTS cluster_members_sym ON cluster_members(symbol_id);
CREATE TABLE IF NOT EXISTS processes(id TEXT PRIMARY KEY, name TEXT, entry TEXT, steps INTEGER, summary TEXT);
CREATE TABLE IF NOT EXISTS process_steps(process_id TEXT, ord INTEGER, symbol_id TEXT);
CREATE INDEX IF NOT EXISTS process_steps_sym ON process_steps(symbol_id);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE VIRTUAL TABLE IF NOT EXISTS symbols_fts USING fts5(
  id UNINDEXED, name, qname, tokens, signature, summary, file, tokenize='unicode61');
"""


def name_tokens(*names: str) -> str:
    """`twoSum` / `two_sum` -> "two sum" so searches match either spelling."""
    out = []
    for n in names:
        n = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", n)
        out.append(re.sub(r"[_.:/#\-]+", " ", n).lower())
    return " ".join(out)


class Store:
    def __init__(self, path: Path, shared: bool = False):
        """`shared=True` lets several threads use this connection (the MCP server serialises them with a lock)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.db = sqlite3.connect(str(path), timeout=30, check_same_thread=not shared)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        if self.db.execute("PRAGMA user_version").fetchone()[0] != DB_VERSION:
            # Older layout: everything here is derived from source, so rebuild instead of migrating.
            for row in list(self.db.execute("SELECT name, type FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%'")):
                if not row["name"].startswith("symbols_fts_"):
                    self.db.execute(f"DROP {row['type']} IF EXISTS \"{row['name']}\"")
            self.db.execute(f"PRAGMA user_version={DB_VERSION}")
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    @contextmanager
    def tx(self) -> Iterator[None]:
        try:
            self.db.execute("BEGIN")
            yield
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    # ------------------------------------------------------------------ meta

    def set_meta(self, **kv: object) -> None:
        self.db.executemany("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", [(k, str(v)) for k, v in kv.items()])

    def meta(self) -> dict[str, str]:
        return {r["key"]: r["value"] for r in self.db.execute("SELECT key, value FROM meta")}

    # ------------------------------------------------------------------ files

    def file_rows(self) -> dict[str, sqlite3.Row]:
        return {r["path"]: r for r in self.db.execute("SELECT * FROM files")}

    def touch_file(self, path: str, size: int, mtime_ns: int) -> None:
        self.db.execute("UPDATE files SET size=?, mtime_ns=? WHERE path=?", (size, mtime_ns, path))

    def remove_file(self, path: str) -> None:
        # FTS rows share their rowid with the symbol row, so this delete is an index lookup, not a table scan.
        self.db.execute("DELETE FROM symbols_fts WHERE rowid IN (SELECT rowid FROM symbols WHERE file=?)", (path,))
        for table in ("symbols", "raw_edges", "imports", "edges", "unresolved"):
            self.db.execute(f"DELETE FROM {table} WHERE file=?", (path,))
        self.db.execute("DELETE FROM files WHERE path=?", (path,))

    def replace_file(self, fp: FileParse, size: int, mtime_ns: int, indexed_at: str) -> None:
        self.remove_file(fp.path)
        self.db.execute(
            "INSERT INTO files(path, lang, sha, size, mtime_ns, line_count, indexed_at, error) VALUES (?,?,?,?,?,?,?,?)",
            (fp.path, fp.lang, fp.sha, size, mtime_ns, fp.line_count, indexed_at, fp.error),
        )
        self.db.executemany(
            "INSERT OR REPLACE INTO symbols(id, file, kind, name, qname, start, end, signature, summary, body_sha) VALUES (?,?,?,?,?,?,?,?,?,?)",
            [(s.id, s.file, s.kind, s.name, s.qname, s.start, s.end, s.signature, s.summary, s.body_sha) for s in fp.symbols],
        )
        rowids = {r["id"]: r["rowid"] for r in self.db.execute("SELECT id, rowid FROM symbols WHERE file=?", (fp.path,))}
        self.db.executemany(
            "INSERT INTO symbols_fts(rowid, id, name, qname, tokens, signature, summary, file) VALUES (?,?,?,?,?,?,?,?)",
            [(rowids[s.id], s.id, s.name, s.qname, name_tokens(s.name, s.qname, s.file), s.signature, s.summary, s.file)
             for s in fp.symbols if s.id in rowids],
        )
        self.db.executemany(
            "INSERT INTO raw_edges(file, type, src, target, line, level, aux, inferred, local, ctx) VALUES (?,?,?,?,?,?,?,?,?,?)",
            [(fp.path, e.type, e.src, e.target, e.line, e.level, e.aux, int(e.inferred), int(e.local), e.ctx) for e in fp.edges],
        )
        self.db.executemany(
            "INSERT INTO imports(file, alias, module, name, level, line) VALUES (?,?,?,?,?,?)",
            [(fp.path, b.alias, b.module, b.name, b.level, b.line) for b in fp.imports],
        )

    # ------------------------------------------------------------------ resolver input / output

    def symbols(self) -> list[Symbol]:
        return [Symbol(r["id"], r["file"], r["kind"], r["name"], r["qname"], r["start"], r["end"], r["signature"], r["summary"], r["body_sha"] or "")
                for r in self.db.execute("SELECT * FROM symbols ORDER BY file, start, id")]

    def raw_edge_list(self) -> list[tuple[str, RawEdge]]:
        return [
            (r["file"], RawEdge(r["type"], r["src"], r["target"], r["line"], r["level"], r["aux"], bool(r["inferred"]), bool(r["local"]), r["ctx"]))
            for r in self.db.execute("SELECT * FROM raw_edges ORDER BY file, rowid")
        ]

    def bindings(self) -> dict[str, Bindings]:
        out: dict[str, Bindings] = {}
        for r in self.db.execute("SELECT * FROM imports ORDER BY file, rowid"):
            b = out.setdefault(r["file"], Bindings())
            if r["alias"] == "*":
                b.stars.append((r["module"], r["level"]))
            else:
                b.by_alias.setdefault(r["alias"], (r["module"], r["name"], r["level"]))
        return out

    def replace_edges(self, resolved: Iterable[Resolved], unresolved: Iterable[Unresolved]) -> None:
        self.db.execute("DELETE FROM edges")
        self.db.execute("DELETE FROM unresolved")
        self.db.executemany(
            "INSERT INTO edges(type, src, dst, line, conf, file) VALUES (?,?,?,?,?,?)",
            [(r.type, r.src, r.dst, r.line, r.conf, r.file) for r in resolved],
        )
        unres = list(unresolved)
        self.db.executemany(
            "INSERT INTO unresolved(src, name, line, candidates, reason, file) VALUES (?,?,?,?,?,?)",
            [(u.src, u.name, u.line, ",".join(u.candidates), u.reason, u.file) for u in unres],
        )
        # Unresolved calls stay visible in the graph as "?name" edges (see graph-schema.md).
        self.db.executemany(
            "INSERT INTO edges(type, src, dst, line, conf, file) VALUES (?, ?, ?, ?, 'low', ?)",
            [(u.type, u.src, f"?{u.name}", u.line, u.file) for u in unres],
        )

    # ------------------------------------------------------------------ search

    def fts(self, text: str, limit: int = 20) -> list[sqlite3.Row]:
        tokens = [t for t in re.findall(r"[A-Za-z0-9]+", name_tokens(text)) if t]
        if not tokens:
            return []
        expr = " OR ".join(f'"{t}"*' for t in tokens[:12])
        return list(self.db.execute(
            "SELECT s.*, bm25(symbols_fts, 0, 6, 4, 5, 1, 2, 0.5) AS score FROM symbols_fts "
            "JOIN symbols s ON s.id = symbols_fts.id WHERE symbols_fts MATCH ? ORDER BY score LIMIT ?",
            (expr, limit),
        ))
