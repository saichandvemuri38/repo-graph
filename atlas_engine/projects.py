"""Registry of indexed projects, so one web app can browse several repositories.

Stored in ~/.codeatlas/projects.json (override the folder with $CODEATLAS_HOME). A project registered with
a `data_dir` keeps its graph there and is never written to; the home project keeps using <root>/CodeAtlas.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .config import Paths


def home_dir() -> Path:
    return Path(os.environ.get("CODEATLAS_HOME") or Path.home() / ".codeatlas").expanduser()


def _registry_file() -> Path:
    return home_dir() / "projects.json"


@dataclass
class Project:
    name: str
    root: Path
    data_dir: Path | None

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
                "edges": int(meta.get("edgeCount", 0) or 0), "lastLink": meta.get("lastLink", "never"),
                "languages": meta.get("languages", "none")}


def _load() -> list[dict]:
    try:
        return json.loads(_registry_file().read_text(encoding="utf-8")).get("projects", [])
    except (OSError, ValueError):
        return []


def _save(rows: list[dict]) -> None:
    home_dir().mkdir(parents=True, exist_ok=True)
    tmp = _registry_file().with_suffix(".tmp")
    tmp.write_text(json.dumps({"projects": rows}, indent=2), encoding="utf-8")
    tmp.replace(_registry_file())


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-") or "project"


def list_projects() -> list[Project]:
    return [Project(r["name"], Path(r["root"]), Path(r["data_dir"]) if r.get("data_dir") else None) for r in _load()]


def get(name: str) -> Project | None:
    return next((p for p in list_projects() if p.name == name), None)


def register(root: str | os.PathLike, name: str | None = None, *, external: bool = True) -> Project:
    """Add (or update) a project. External projects keep their graph under ~/.codeatlas/projects/<name>."""
    root = Path(root).expanduser().resolve()
    rows = _load()
    existing = next((r for r in rows if Path(r["root"]) == root), None)
    if existing:
        return Project(existing["name"], root, Path(existing["data_dir"]) if existing.get("data_dir") else None)
    base = slug(name or root.name)
    taken = {r["name"] for r in rows}
    unique, n = base, 2
    while unique in taken:
        unique, n = f"{base}-{n}", n + 1
    data_dir = str(home_dir() / "projects" / unique) if external else None
    rows.append({"name": unique, "root": str(root), "data_dir": data_dir})
    _save(rows)
    return Project(unique, root, Path(data_dir) if data_dir else None)


def remove(name: str) -> bool:
    """Forget a project. Its source folder is never touched; its graph data folder is left for you to delete."""
    rows = _load()
    kept = [r for r in rows if r["name"] != name]
    if len(kept) == len(rows):
        return False
    _save(kept)
    return True
