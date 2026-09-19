"""Paths, limits and language tables shared by every module."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

APP_DIR = "CodeAtlas"
SCHEMA_VERSION = "1"
MAX_FILE_BYTES = 1_000_000   # larger files are skipped (and listed in the status bar); raise or ignore via .codeatlasignore

EXCLUDE_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "venv", ".venv", "env", "__pycache__",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", "dist", "build", "target",
    "vendor", ".next", ".idea", ".vscode", "site-packages", ".claude", APP_DIR,
}

EXT_LANG = {
    ".py": "python",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "tsx",
    ".java": "java",
    ".go": "go",
}

JS_LANGS = {"javascript", "typescript", "tsx"}


@dataclass(frozen=True)
class Paths:
    root: Path
    data: Path | None = None   # where graph data and generated pages live; default is <root>/CodeAtlas

    @property
    def app(self) -> Path:
        return self.data or self.root / APP_DIR

    @property
    def graph(self) -> Path:
        return self.app / "graph"

    @property
    def shards(self) -> Path:
        return self.graph / "shards"

    @property
    def reports(self) -> Path:
        return self.app / "reports"

    @property
    def db(self) -> Path:
        return self.graph / "atlas.db"

    @property
    def lock(self) -> Path:
        return self.graph / ".index.lock"


def find_root(start: str | os.PathLike | None = None) -> Path:
    """Project root: $CODEATLAS_ROOT, else the nearest ancestor holding a CodeAtlas/ folder, else `start`."""
    env = os.environ.get("CODEATLAS_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    here = Path(start or os.getcwd()).resolve()
    for candidate in (here, *here.parents):
        if (candidate / APP_DIR).is_dir():
            return candidate
    return here
