"""Monorepo and alias resolution for JavaScript/TypeScript imports.

Reads `package.json` (name + exports/main) and `tsconfig.json`/`jsconfig.json` (baseUrl + paths) so that
`import { Button } from "@repo/ui/button"` and `import x from "@/lib/x"` resolve to real files in the repo.
`collect` produces plain JSON-able data (stored in the graph meta); `candidates` turns an import into base paths
that the resolver then tries with file extensions.
"""

from __future__ import annotations

import json
import posixpath
import re
from pathlib import Path

CONDITIONS = ("import", "default", "require", "types", "module", "browser")


def _strip_jsonc(text: str) -> str:
    """Remove // and /* */ comments (outside strings) and trailing commas, so tsconfig.json parses as JSON."""
    out, i, n, in_str = [], 0, len(text), False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1]); i += 1
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True; out.append(c)
        elif text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
            continue
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        else:
            out.append(c)
        i += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))


def _load_json(path: Path, jsonc: bool = False):
    try:
        raw = path.read_text(encoding="utf-8")
        return json.loads(_strip_jsonc(raw) if jsonc else raw)
    except (OSError, ValueError):
        return None


def collect(root: Path, all_paths: list[str]) -> dict:
    """Workspace packages and tsconfig aliases found under `root`. `all_paths` are relative file paths."""
    packages, aliases = [], []
    for rel in all_paths:
        if "node_modules/" in rel or rel.startswith("node_modules/"):
            continue
        name = posixpath.basename(rel)
        folder = posixpath.dirname(rel)
        if name == "package.json":
            data = _load_json(root / rel)
            if isinstance(data, dict) and isinstance(data.get("name"), str):
                packages.append({"name": data["name"], "dir": folder, "exports": data.get("exports"),
                                 "main": data.get("module") or data.get("main") or ""})
        elif name in ("tsconfig.json", "jsconfig.json"):
            data = _load_json(root / rel, jsonc=True)
            opts = data.get("compilerOptions") if isinstance(data, dict) else None
            if isinstance(opts, dict) and (opts.get("paths") or opts.get("baseUrl")):
                aliases.append({"dir": folder, "base": opts.get("baseUrl") or ".",
                                "paths": {k: v for k, v in (opts.get("paths") or {}).items() if isinstance(v, list)}})
    packages.sort(key=lambda p: (-len(p["name"]), p["name"]))
    aliases.sort(key=lambda a: -len(a["dir"]))
    return {"packages": packages, "aliases": aliases}


def _match(pattern: str, spec: str) -> str | None:
    """Return the text `*` stands for when `spec` matches `pattern`, "" for an exact match, else None."""
    if "*" not in pattern:
        return "" if pattern == spec else None
    prefix, _, suffix = pattern.partition("*")
    if spec.startswith(prefix) and spec.endswith(suffix) and len(spec) >= len(prefix) + len(suffix):
        return spec[len(prefix):len(spec) - len(suffix)]
    return None


def _target(value) -> str | None:
    """An exports value is a string or a conditions object; take the first usable string."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in CONDITIONS:
            if key in value:
                found = _target(value[key])
                if found:
                    return found
        for v in value.values():
            found = _target(v)
            if found:
                return found
    return None


def candidates(spec: str, importer: str, ws: dict | None) -> list[str]:
    """Base paths (no extension guessing) for a non-relative import specifier."""
    if not ws or spec.startswith("."):
        return []
    out: list[str] = []
    importer_dir = posixpath.dirname(importer)
    for alias in ws.get("aliases", []):
        d = alias["dir"]
        if d and importer_dir != d and not importer_dir.startswith(d + "/"):
            continue
        base = posixpath.normpath(posixpath.join(d, alias["base"]))
        for pattern, targets in alias["paths"].items():
            mid = _match(pattern, spec)
            if mid is not None:
                out += [posixpath.normpath(posixpath.join(base, t.replace("*", mid))) for t in targets]
        if not spec.startswith("@") and alias["base"] not in (".", ""):
            out.append(posixpath.normpath(posixpath.join(base, spec)))       # baseUrl-relative import
    for pkg in ws.get("packages", []):
        name = pkg["name"]
        if spec != name and not spec.startswith(name + "/"):
            continue
        sub = "." + spec[len(name):]
        exports = pkg.get("exports")
        if isinstance(exports, dict) and any(k.startswith(".") for k in exports):
            for key, value in exports.items():
                mid = _match(key, sub)
                target = _target(value)
                if mid is not None and target:
                    out.append(posixpath.normpath(posixpath.join(pkg["dir"], target.replace("*", mid))))
        elif isinstance(exports, str) and sub == ".":
            out.append(posixpath.normpath(posixpath.join(pkg["dir"], exports)))
        if sub == "." and pkg.get("main"):
            out.append(posixpath.normpath(posixpath.join(pkg["dir"], pkg["main"])))
        tail = sub[2:]
        for guess in ([f"src/{tail}", tail] if tail else ["src/index", "index"]):
            out.append(posixpath.normpath(posixpath.join(pkg["dir"], guess)))
    seen: set[str] = set()
    return [c for c in out if not (c in seen or seen.add(c))]
