"""Incremental indexing pipeline.

discover -> stat/hash -> diff against the DB -> parse only changed files -> re-resolve all edges
-> clusters + processes -> export text shards -> render HTML.

Parsing is the expensive part and is incremental. Resolution is a set of dict lookups, so it is redone
for the whole graph on every run; that keeps cross-file edges correct when a file changes.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator

from . import cluster, shards, workspace
from .config import EXCLUDE_DIRS, EXT_LANG, JS_LANGS, MAX_FILE_BYTES, SCHEMA_VERSION, Paths
from .model import FileParse
from .parsers import available_languages, parse_file
from .resolve import Resolver
from .store import Store


@dataclass
class IndexResult:
    scanned: int = 0
    added: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    unchanged: int = 0
    skipped: list[tuple[str, str]] = field(default_factory=list)
    parse_errors: list[tuple[str, str]] = field(default_factory=list)
    symbols: int = 0
    edges: int = 0
    unresolved: int = 0
    cluster_algo: str = ""
    seconds: float = 0.0

    @property
    def changed(self) -> bool:
        return bool(self.added or self.modified or self.removed)

    def summary(self) -> str:
        parts = [f"{self.scanned} files", f"+{len(self.added)} ~{len(self.modified)} -{len(self.removed)}",
                 f"{self.symbols} symbols", f"{self.edges} edges", f"{self.unresolved} unresolved", f"{self.seconds:.2f}s"]
        return " | ".join(parts)


# ------------------------------------------------------------------------------------ discovery


def _git_files(root: Path) -> list[str] | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            capture_output=True, timeout=30, check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return [p for p in out.decode("utf-8", "replace").split("\0") if p]


def _walk_files(root: Path) -> list[str]:
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDE_DIRS and not d.startswith("."))
        rel_dir = os.path.relpath(dirpath, root)
        for name in sorted(filenames):
            found.append(name if rel_dir == "." else f"{rel_dir}/{name}".replace(os.sep, "/"))
    return found


def _ignore_patterns(root: Path) -> list[str]:
    """Lines of `.codeatlasignore`: a path prefix (`tests/`) or a glob (`*_pb2.py`); `#` starts a comment."""
    try:
        lines = (root / ".codeatlasignore").read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [l.strip() for l in lines if l.strip() and not l.strip().startswith("#")]


def _ignored(rel: str, patterns: list[str]) -> bool:
    for pat in patterns:
        if pat.endswith("/") and (rel.startswith(pat) or f"/{pat}" in f"/{rel}"):
            return True
        if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(os.path.basename(rel), pat) or rel == pat:
            return True
    return False


def discover(root: Path) -> list[str]:
    """Relative paths of indexable source files, respecting .gitignore (in a git repo) and .codeatlasignore."""
    candidates = _git_files(root)
    if candidates is None:
        candidates = _walk_files(root)
    patterns = _ignore_patterns(root)
    keep = []
    for rel in sorted(set(candidates)):
        parts = rel.split("/")
        if any(p in EXCLUDE_DIRS for p in parts[:-1]) or _ignored(rel, patterns):
            continue
        if os.path.splitext(parts[-1])[1].lower() in EXT_LANG and (root / rel).is_file():
            keep.append(rel)
    return keep


def sha12(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:12]


@contextmanager
def index_lock(path: Path) -> Iterator[None]:
    """Serialise concurrent index runs (git hooks and editor hooks can overlap)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+")
    try:
        try:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX)
        except ImportError:
            pass
        yield
    finally:
        handle.close()


# ------------------------------------------------------------------------------------ pipeline


def scan_changes(root: Path, store: Store, full: bool = False):
    """Classify files as added / modified / removed / unchanged. Returns the diff plus the loaded file contents."""
    rows = store.file_rows()
    usable = available_languages()
    result = IndexResult()
    to_parse: dict[str, tuple[bytes, str, int, int]] = {}
    touch: list[tuple[str, int, int]] = []
    current = discover(root)
    result.scanned = len(current)
    seen: set[str] = set()
    for rel in current:
        lang = EXT_LANG[os.path.splitext(rel)[1].lower()]
        if lang not in usable:
            result.skipped.append((rel, f"no parser for {lang} (install tree-sitter-language-pack)"))
            continue
        full_path = root / rel
        try:
            st = full_path.stat()
        except OSError:
            continue
        if st.st_size > MAX_FILE_BYTES:
            result.skipped.append((rel, f"larger than {MAX_FILE_BYTES // 1000} KB"))
            continue
        seen.add(rel)
        row = rows.get(rel)
        if row and not full and row["size"] == st.st_size and row["mtime_ns"] == st.st_mtime_ns:
            result.unchanged += 1
            continue
        data = full_path.read_bytes()
        if b"\0" in data[:4096]:
            result.skipped.append((rel, "binary file"))
            seen.discard(rel)
            continue
        digest = sha12(data)
        if row and not full and row["sha"] == digest:
            touch.append((rel, st.st_size, st.st_mtime_ns))
            result.unchanged += 1
            continue
        (result.modified if row else result.added).append(rel)
        to_parse[rel] = (data, lang, st.st_size, st.st_mtime_ns)
    result.removed = sorted(set(rows) - seen)
    return result, to_parse, touch


def index(
    root: Path,
    *,
    full: bool = False,
    publish: bool = True,
    log: Callable[[str], None] | None = None,
    data_dir: Path | None = None,
) -> IndexResult:
    started = time.time()
    paths = Paths(root, data_dir)
    say = log or (lambda _msg: None)
    with index_lock(paths.lock):
        store = Store(paths.db)
        try:
            result, to_parse, touch = scan_changes(root, store, full)
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            meta = store.meta()
            uses_js = _has_js(store) or any(v[1] in JS_LANGS for v in to_parse.values())
            ws = workspace.collect(root, _all_paths(root)) if uses_js else {}
            ws_json = json.dumps(ws, sort_keys=True)
            structure_changed = (result.changed or not meta.get("lastLink") or meta.get("lastLink") == "never"
                                 or ws_json != meta.get("workspace", "{}"))
            with store.tx():
                for rel, size, mtime in touch:
                    store.touch_file(rel, size, mtime)
                for rel in result.removed:
                    store.remove_file(rel)
                for rel, (data, lang, size, mtime) in sorted(to_parse.items()):
                    text = data.decode("utf-8", "replace")
                    try:
                        fp = parse_file(rel, lang, text, sha12(data))
                    except Exception as exc:  # a parser bug must not take the whole index down
                        fp = FileParse(rel, lang, sha12(data), text.count("\n") + 1, error=f"parser crashed: {exc!r}")
                    if fp is None:
                        result.skipped.append((rel, f"no parser for {lang}"))
                        continue
                    if fp.error:
                        result.parse_errors.append((rel, fp.error))
                    store.replace_file(fp, size, mtime, now)
                    say(f"parsed {rel}")
                if structure_changed:
                    files = {r["path"]: r["lang"] for r in store.db.execute("SELECT path, lang FROM files")}
                    resolved, unresolved = Resolver(files, store.symbols(), store.bindings(), store.raw_edge_list(), ws).run()
                    store.replace_edges(resolved, unresolved)
                    store.set_meta(workspace=ws_json)
                    result.cluster_algo = cluster.compute(store)
                    store.set_meta(lastLink=now, lastCluster=now, clusterAlgo=result.cluster_algo)
                store.set_meta(skipped=json.dumps(result.skipped[:200]), skippedCount=len(result.skipped))
                if full:
                    store.set_meta(lastFullIndex=now)
                store.set_meta(schemaVersion=SCHEMA_VERSION)
            counts = store.db.execute(
                "SELECT (SELECT COUNT(*) FROM files) f, (SELECT COUNT(*) FROM symbols) s, "
                "(SELECT COUNT(*) FROM edges WHERE dst NOT LIKE '?%') e, (SELECT COUNT(*) FROM unresolved) u"
            ).fetchone()
            result.symbols, result.edges, result.unresolved = counts["s"], counts["e"], counts["u"]
            meta = store.meta()
            store.set_meta(
                fileCount=counts["f"], symbolCount=counts["s"], edgeCount=counts["e"], unresolvedCount=counts["u"],
                languages=",".join(sorted({r["lang"] for r in store.db.execute("SELECT DISTINCT lang FROM files")})) or "none",
                lastFullIndex=meta.get("lastFullIndex", now if full else "never"),
            )
            store.db.commit()
            if structure_changed or not (paths.graph / "meta.md").exists():
                shards.export(store, paths)
            if publish and (structure_changed or not (paths.app / "index.html").exists() or _pages_are_placeholders(paths)):
                from . import render
                render.publish_all(store, paths)
            result.seconds = time.time() - started
            return result
        finally:
            store.close()


def _all_paths(root: Path) -> list[str]:
    """Every non-ignored file (package.json and tsconfig.json included), for workspace discovery."""
    found = _git_files(root)
    if found is None:
        found = _walk_files(root)
    return [p for p in found if not any(part in EXCLUDE_DIRS for part in p.split("/")[:-1])]


def _has_js(store: Store) -> bool:
    return bool(store.db.execute("SELECT 1 FROM files WHERE lang IN ('javascript','typescript','tsx') LIMIT 1").fetchone())


def _pages_are_placeholders(paths: Paths) -> bool:
    page = paths.app / "index.html"
    try:
        return "not been built yet" in page.read_text(encoding="utf-8")
    except OSError:
        return True
