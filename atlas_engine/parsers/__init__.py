"""Parser registry. Python is always available; other languages need tree-sitter."""

from __future__ import annotations

import hashlib

from ..model import FileParse
from .python_ast import parse_python


def available_languages() -> set[str]:
    langs = {"python"}
    try:
        from . import treesitter
        langs |= treesitter.available()
    except Exception:  # tree-sitter not installed
        pass
    return langs


def parse_file(path: str, lang: str, source: str, sha: str) -> FileParse | None:
    """Parse one file, or return None when no parser is available for its language."""
    if lang == "python":
        fp = parse_python(path, source, sha)
    else:
        try:
            from . import treesitter
        except Exception:
            return None
        fp = treesitter.parse(path, lang, source, sha)
    if fp is not None:
        _add_body_hashes(fp, source)
    return fp


def _h(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:12]


def _add_body_hashes(fp: FileParse, source: str) -> None:
    """Hash each symbol's own lines. The module symbol hashes only the lines no other symbol covers."""
    lines = source.splitlines()
    covered: set[int] = set()
    for s in fp.symbols:
        if s.kind != "module" and "." not in s.qname:
            covered.update(range(s.start, s.end + 1))
    for s in fp.symbols:
        if s.kind == "module":
            s.body_sha = _h("\n".join(l for n, l in enumerate(lines, 1) if n not in covered))
        else:
            s.body_sha = _h("\n".join(lines[s.start - 1:s.end]))
