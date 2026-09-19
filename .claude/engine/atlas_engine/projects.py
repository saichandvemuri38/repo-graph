"""A project: a repository root and where its graph data lives."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .config import Paths


@dataclass
class Project:
    name: str
    root: Path
    data_dir: Path | None = None       # None: the graph lives in <root>/.claude/atlas

    @property
    def paths(self) -> Paths:
        return Paths(self.root, self.data_dir)

    def stats(self) -> dict | None:
        """Counts from the project's database, read without creating or locking it."""
        db = self.paths.db
        if not db.is_file():
            return None
        try:
            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
            conn.row_factory = sqlite3.Row
            meta = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM meta")}
            conn.close()
        except sqlite3.Error:
            return None
        return {"files": int(meta.get("fileCount", 0) or 0), "symbols": int(meta.get("symbolCount", 0) or 0),
                "edges": int(meta.get("edgeCount", 0) or 0), "lastLink": meta.get("lastLink", "never"), "linkCount": meta.get("linkCount", "0"),
                "languages": meta.get("languages", "none")}
