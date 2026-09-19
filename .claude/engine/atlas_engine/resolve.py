"""Cross-file name resolution. Implements .claude/prompts/resolution-rules.md in code.

Order (first unambiguous answer wins):
  1. lexical scope in the same file (nested functions, then module level)
  2. self/this/super through the class hierarchy
  3. import bindings (module imports, from-imports, re-exports, star imports)
  4. unique global name            -> "med"
  5. nearest of several candidates -> "low"
  6. otherwise unresolved, with the candidates recorded
Receiver types inferred by the parser cap the confidence at "med".
"""

from __future__ import annotations

import posixpath
import re
from collections import defaultdict
from dataclasses import dataclass, field, replace

from . import workspace as ws_mod
from .config import JS_LANGS
from .model import RawEdge, Symbol

BUILTINS = {
    "print", "len", "range", "enumerate", "zip", "map", "filter", "sorted", "reversed", "sum", "min", "max",
    "abs", "any", "all", "isinstance", "issubclass", "type", "int", "str", "float", "bool", "list", "dict",
    "set", "tuple", "frozenset", "bytes", "bytearray", "open", "super", "iter", "next", "input", "repr",
    "format", "hash", "id", "round", "divmod", "pow", "getattr", "setattr", "hasattr", "delattr", "callable",
    "property", "staticmethod", "classmethod", "object", "Exception", "BaseException", "ValueError",
    "TypeError", "KeyError", "IndexError", "RuntimeError", "NotImplementedError", "StopIteration",
    "OSError", "AttributeError", "ABC", "Enum", "chr", "ord", "hex", "bin", "oct", "vars", "dir", "slice",
    "console", "Math", "JSON", "Object", "Array", "Promise", "Number", "String", "Boolean", "Map", "Set",
    "Error", "Symbol", "Date", "RegExp", "setTimeout", "setInterval", "fetch", "parseInt", "parseFloat",
    "require", "fmt", "make", "new", "append", "cap", "copy", "panic", "recover", "String", "System",
    # JavaScript / TypeScript globals and test-runner globals
    "URL", "URLSearchParams", "clearTimeout", "clearInterval", "setImmediate", "queueMicrotask", "structuredClone", "process",
    "Buffer", "Reflect", "Intl", "WeakMap", "WeakSet", "ReadonlySet", "ReadonlyMap", "BigInt", "Uint8Array", "Float32Array",
    "encodeURIComponent", "decodeURIComponent", "isNaN", "globalThis", "window", "document", "describe", "it", "test", "expect",
    "beforeEach", "afterEach", "beforeAll", "afterAll", "vi", "jest", "Proxy", "TextDecoder", "TextEncoder", "AbortController",
    "Response", "Request", "Headers", "FormData", "Blob", "Atomics", "SharedArrayBuffer", "Function", "Iterator",
}
MAX_REPORTED_CANDIDATES = 12   # a name shared by more symbols than this says nothing useful; do not list it as ambiguous

IGNORABLE_METHODS = {
    "append", "extend", "insert", "pop", "get", "items", "keys", "values", "update", "add", "remove", "discard",
    "join", "split", "strip", "lstrip", "rstrip", "lower", "upper", "format", "replace", "startswith", "endswith",
    "find", "index", "count", "sort", "reverse", "copy", "clear", "setdefault", "popleft", "appendleft",
    "encode", "decode", "read", "write", "close", "push", "shift", "unshift", "slice", "splice", "concat",
    "map", "filter", "reduce", "forEach", "then", "catch", "toString", "length", "size", "equals", "hashCode",
    "put", "contains", "isEmpty", "next", "toArray", "stream", "collect", "println", "printf", "Println",
    "Printf", "Sprintf", "Errorf", "Error", "String",
}

CALLABLE_KINDS = ("function", "method", "class")


@dataclass
class Resolved:
    type: str
    src: str
    dst: str
    line: int
    conf: str
    file: str


@dataclass
class Unresolved:
    src: str
    name: str
    line: int
    candidates: list[str]
    reason: str
    file: str
    type: str = "CALLS"


@dataclass
class Bindings:
    """Import bindings of one file, keyed by the alias they introduce."""
    by_alias: dict[str, tuple[str, str | None, int]] = field(default_factory=dict)   # alias -> (module, name, level)
    stars: list[tuple[str, int]] = field(default_factory=list)                        # (module, level)


class Resolver:
    def __init__(
        self,
        files: dict[str, str],
        symbols: list[Symbol],
        imports: dict[str, Bindings],
        raw_edges: list[tuple[str, RawEdge]],
        workspace: dict | None = None,
    ):
        self.files = files
        self.workspace = workspace or {}
        self.imports = imports
        self.raw = raw_edges
        self.sym = {s.id: s for s in symbols}
        self.by_fq: dict[tuple[str, str], str] = {}
        self.by_name: dict[str, list[Symbol]] = defaultdict(list)
        self.top: dict[str, dict[str, str]] = defaultdict(dict)
        self.members: dict[tuple[str, str], dict[str, str]] = defaultdict(dict)
        for s in symbols:
            self.by_fq[(s.file, s.qname)] = s.id
            if s.kind == "module":
                continue
            self.by_name[s.name].append(s)
            if "." not in s.qname:
                self.top[s.file].setdefault(s.name, s.id)
            else:
                parent = s.qname.rpartition(".")[0]
                self.members[(s.file, parent)].setdefault(s.name, s.id)
        self.bases: dict[str, list[str]] = defaultdict(list)
        self._modcache: dict[tuple, list[str]] = {}
        self._dirs: dict[str, list[str]] | None = None
        self.out: list[Resolved] = []
        self.unres: list[Unresolved] = []

    # ------------------------------------------------------------------ entry point

    def run(self) -> tuple[list[Resolved], list[Unresolved]]:
        self._defines()
        for file, e in self.raw:
            if e.type == "IMPORTS":
                self._import_edge(file, e)
        for file, e in self.raw:
            if e.type == "EXTENDS":
                self._extends(file, e)
        for file, e in self.raw:
            if e.type in ("CALLS", "REFERENCES"):
                self._use(file, e)
        seen: set[tuple] = set()
        deduped = []
        for r in self.out:
            key = (r.type, r.src, r.dst, r.line)
            if key not in seen:
                seen.add(key)
                deduped.append(r)
        return deduped, self.unres

    # ------------------------------------------------------------------ containment and imports

    def _defines(self) -> None:
        for s in self.sym.values():
            parent = s.qname.rpartition(".")[0] if s.kind != "module" else ""
            src = self.by_fq.get((s.file, parent), s.file) if parent else s.file
            self.out.append(Resolved("DEFINES", src, s.id, s.start, "high", s.file))

    def _import_edge(self, file: str, e: RawEdge) -> None:
        lang = self.files.get(file, "")
        targets = self.module_files(file, e.target, e.level)
        if lang == "python" and e.aux:
            # `from pkg import mod` may import a submodule file rather than a name.
            for name in e.aux.split(","):
                sub = self.module_files(file, f"{e.target}.{name}" if e.target else name, e.level)
                if sub and not self.top.get(targets[0] if targets else "", {}).get(name):
                    targets = sorted(set(targets) | set(sub))
        if lang == "java" and e.aux:
            targets = [t for t in targets if e.aux in self.top.get(t, {})] or targets[:1]
        if targets:
            for t in targets[:8]:
                self.out.append(Resolved("IMPORTS", file, t, e.line, "high", file))
        elif e.target:
            top = e.target.split(".")[0] if not e.target.startswith(".") else e.target
            self.out.append(Resolved("IMPORTS", file, f"ext:{top}", e.line, "high", file))

    # ------------------------------------------------------------------ module lookup

    def module_files(self, importer: str, module: str, level: int = 0) -> list[str]:
        key = (importer, module, level)
        if key not in self._modcache:
            lang = self.files.get(importer, "")
            if lang == "python":
                found = self._py_module(importer, module, level)
            elif lang in JS_LANGS:
                found = self._js_module(importer, module)
            elif lang in ("java", "go"):
                found = self._dir_module(lang, module)
            else:
                found = []
            self._modcache[key] = found
        return self._modcache[key]

    def _py_module(self, importer: str, module: str, level: int) -> list[str]:
        parts = [p for p in module.split(".") if p]
        imp_dir = posixpath.dirname(importer)
        if level:
            base = imp_dir
            for _ in range(level - 1):
                base = posixpath.dirname(base)
            roots = [base]
        else:
            roots = ["", imp_dir]
        for root in roots:
            path = posixpath.join(root, *parts) if parts else root
            candidates = [path + ".py", posixpath.join(path, "__init__.py")] if parts else [posixpath.join(path, "__init__.py")]
            for cand in candidates:
                if cand in self.files:
                    return [cand]
        if parts and not level:
            tail = "/".join(parts)
            for f, lang in self.files.items():
                if lang == "python" and (f.endswith("/" + tail + ".py") or f.endswith("/" + tail + "/__init__.py")):
                    return [f]
        return []

    def _js_module(self, importer: str, spec: str) -> list[str]:
        if spec.startswith("."):
            bases = [posixpath.normpath(posixpath.join(posixpath.dirname(importer), spec))]
        else:
            bases = ws_mod.candidates(spec, importer, self.workspace)    # tsconfig paths and workspace packages
        exts = ("", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
        for base in bases:
            for ext in exts:
                if base + ext in self.files:
                    return [base + ext]
            for ext in exts[1:]:
                if f"{base}/index{ext}" in self.files:
                    return [f"{base}/index{ext}"]
        return []

    def _package_files(self, file: str) -> list[str]:
        """Files of the same language in the same directory: one package namespace in Go and Java."""
        self._dir_index()
        return [f for f in self._dirs.get(f"{self.files.get(file)}:{posixpath.dirname(file)}", []) if f != file]

    def _dir_index(self) -> None:
        if self._dirs is None:
            self._dirs = defaultdict(list)
            for f, fl in self.files.items():
                self._dirs[f"{fl}:{posixpath.dirname(f)}"].append(f)

    def _dir_module(self, lang: str, module: str) -> list[str]:
        """Java packages and Go import paths map to directories: every file of that language in the directory."""
        self._dir_index()
        wanted = module.replace(".", "/") if lang == "java" else module
        out: list[str] = []
        for key, members in self._dirs.items():
            key_lang, _, directory = key.partition(":")
            if key_lang != lang or not directory:
                continue
            if directory == wanted or wanted.endswith("/" + directory) or directory.endswith("/" + wanted):
                out.extend(members)
        return sorted(out)

    # ------------------------------------------------------------------ hierarchy

    def _extends(self, file: str, e: RawEdge) -> None:
        src = self.sym.get(e.src)
        found = self._lookup(file, src, e.target.split("."), e.ctx, kinds=("class",))
        if found == "external":
            return
        if found:
            dst, conf = found
            self.out.append(Resolved("EXTENDS", e.src, dst, e.line, conf, file))
            if dst not in self.bases[e.src]:
                self.bases[e.src].append(dst)
        elif e.target.split(".")[0] not in BUILTINS and not self._is_external(file, e.target.split(".")[0]):
            self.unres.append(Unresolved(e.src, e.target, e.line, [], "no-definition", file, "EXTENDS"))

    def member(self, class_id: str, name: str, seen: set[str] | None = None) -> str | None:
        cls = self.sym[class_id]
        found = self.members.get((cls.file, cls.qname), {}).get(name)
        if found:
            return found
        seen = seen if seen is not None else set()
        seen.add(class_id)
        for base in self.bases.get(class_id, []):
            if base not in seen:
                found = self.member(base, name, seen)
                if found:
                    return found
        return None

    def _descend(self, sid: str, rest: list[str]) -> str | None:
        for name in rest:
            s = self.sym[sid]
            nxt = self.member(sid, name) if s.kind == "class" else self.members.get((s.file, s.qname), {}).get(name)
            if not nxt:
                return None
            sid = nxt
        return sid

    # ------------------------------------------------------------------ CALLS and REFERENCES

    def _use(self, file: str, e: RawEdge) -> None:
        src = self.sym.get(e.src)
        quiet = e.type == "REFERENCES"          # a reference that resolves to nothing is just a variable
        if e.target.startswith("@ret:"):
            hit = self._via_return_type(file, src, e)
            if hit:
                self.out.append(Resolved(e.type, e.src, hit, e.line, "med", file))
            else:
                # Result of a call we could not type (often a library): only report real ambiguity, not "no such method".
                callee, _, rest = e.target[len("@ret:"):].partition(":")
                shown = replace(e, target=f"{callee}(...).{rest}")
                self._fallback(file, shown, ["*", *rest.split(".")], quiet, record_missing=False)
            return
        parts = e.target.split(".")
        if e.local:
            return
        found = self._lookup(file, src, parts, e.ctx, kinds=CALLABLE_KINDS)
        if found == "external":
            return
        if found:
            dst, conf = found
            if e.inferred and conf == "high":
                conf = "med"
            self.out.append(Resolved(e.type, e.src, dst, e.line, conf, file))
            return
        self._fallback(file, e, parts, quiet)

    def _via_return_type(self, file: str, src: Symbol | None, e: RawEdge) -> str | None:
        """`x = make()` then `x.run()`: find `make`, read its `-> Type` annotation, look `run` up on Type."""
        callee, _, rest = e.target[len("@ret:"):].partition(":")
        hit = self._lookup(file, src, callee.split("."), e.ctx, kinds=CALLABLE_KINDS)
        if not hit or hit == "external":
            return None
        fn = self.sym[hit[0]]
        if fn.kind == "class":
            cls_id = fn.id
        else:
            m = re.search(r"->\s*(.+?)\s*:?\s*$", fn.signature)                              # Python: -> Foo
            typ = _return_type(m.group(1)) if m else None
            if not typ:                                                                        # TypeScript: ): Foo  or  ): Promise<Foo>
                t = re.search(r"\)\s*:\s*(?:Promise<\s*)?([A-Za-z_$][\w$.]*)", fn.signature)
                typ = _return_type(t.group(1)) if t else None
            found = self._lookup(fn.file, fn, typ.split("."), "", kinds=("class",)) if typ else None
            if not found or found == "external":
                return None
            cls_id = found[0]
        target = self._descend(cls_id, rest.split("."))
        return target if target and self.sym[target].kind in CALLABLE_KINDS else None

    def _lookup(self, file: str, src: Symbol | None, parts: list[str], ctx: str, kinds: tuple[str, ...]):
        """Return (symbol id, confidence), "external", or None."""
        head, rest = parts[0], parts[1:]
        cls_id = self.by_fq.get((file, ctx)) if ctx else None
        if not rest and cls_id and src and src.id == cls_id:      # class body: `visit_List = _visit_sequence` names a sibling method
            sibling = self.member(cls_id, head)
            if sibling:
                return sibling, "high"
        if head in ("self", "cls", "this") and cls_id:
            if len(rest) == 1:
                hit = self.member(cls_id, rest[0])
                return (hit, "high") if hit else None
            return None
        if head == "super" and cls_id and len(rest) == 1:
            for base in self.bases.get(cls_id, []):
                hit = self.member(base, rest[0])
                if hit:
                    return hit, "high"
            return None
        bound = self._bind(file, src, head)
        if bound is None:
            return None
        if bound == "external":
            return "external"
        if isinstance(bound, _ModuleRef):
            return self._in_module(bound, rest, kinds)
        target = self._descend(bound, rest) if rest else bound
        if target and self.sym[target].kind in kinds:
            return target, "high"
        return None

    def _in_module(self, ref: "_ModuleRef", rest: list[str], kinds: tuple[str, ...]):
        """`mod.func` / `pkg.sub.Class.method`: extend the module path while it names a file, then look inside."""
        i = 0
        while i < len(rest):
            nxt = self.module_files(ref.importer, f"{ref.module}.{rest[i]}", ref.level)
            if not nxt:
                break
            ref = _ModuleRef(f"{ref.module}.{rest[i]}", ref.level, nxt, ref.importer)
            i += 1
        remaining = rest[i:]
        if not remaining:
            return None
        for f in ref.files:
            hit = self.top.get(f, {}).get(remaining[0])
            if hit:
                target = self._descend(hit, remaining[1:]) if len(remaining) > 1 else hit
                if target and self.sym[target].kind in kinds:
                    return target, "high"
        return None

    def _bind(self, file: str, src: Symbol | None, head: str, depth: int = 0):
        """Resolve the first name of a dotted path to a symbol id, "external", or None."""
        if src and src.kind != "module":
            q = src.qname
            while q:
                anc = self.by_fq.get((file, q))
                if anc and self.sym[anc].kind in ("function", "method"):
                    nested = self.by_fq.get((file, f"{q}.{head}"))
                    if nested:
                        return nested
                q = q.rpartition(".")[0]
        local = self.top.get(file, {}).get(head)
        if local:
            return local
        if self.files.get(file) in ("go", "java"):
            for sibling in self._package_files(file):
                hit = self.top.get(sibling, {}).get(head)
                if hit:
                    return hit
        b = self.imports.get(file)
        if b and head in b.by_alias and depth < 4:
            module, name, level = b.by_alias[head]
            return self._bound_import(file, module, name, level, depth, alias=head)
        if b and depth < 4:
            for module, level in b.stars:
                for f in self.module_files(file, module, level):
                    hit = self.top.get(f, {}).get(head)
                    if hit:
                        return hit
        return None

    def _default_export(self, f: str, alias: str | None) -> str | None:
        """`import X from "./x"`: the symbol named X, else the file's only top-level function/class, else the one named like the file."""
        top = {n: i for n, i in self.top.get(f, {}).items() if self.sym[i].kind in ("function", "class")}
        if alias and alias in top:
            return top[alias]
        if len(top) == 1:
            return next(iter(top.values()))
        stem = posixpath.splitext(posixpath.basename(f))[0].lower()
        return next((i for n, i in top.items() if n.lower() == stem), None)

    def _bound_import(self, file: str, module: str, name: str | None, level: int, depth: int, alias: str | None = None):
        files = self.module_files(file, module, level)
        if name == "default":
            for f in files:
                hit = self._default_export(f, alias)
                if hit:
                    return hit
        if name is None or name == "default":
            # `import module`: attribute access continues into that module, handled by _ModuleRef below.
            return _ModuleRef(module, level, files, file) if files else "external"
        for f in files:
            hit = self.top.get(f, {}).get(name)
            if hit:
                return hit
            reexport = self.imports.get(f)
            if reexport and name in reexport.by_alias and depth < 3:
                m2, n2, l2 = reexport.by_alias[name]
                hit = self._bound_import(f, m2, n2, l2, depth + 1)
                if isinstance(hit, str) and hit != "external":
                    return hit
        sub = self.module_files(file, f"{module}.{name}" if module else name, level)
        if sub:
            return _ModuleRef(f"{module}.{name}" if module else name, level, sub, file)
        return "external" if not files else None

    # ------------------------------------------------------------------ global fallback

    def _fallback(self, file: str, e: RawEdge, parts: list[str], quiet: bool, record_missing: bool = True) -> None:
        name = parts[-1]
        multi = len(parts) > 1
        if not multi and name in BUILTINS:
            return                      # a language built-in, and nothing in scope or imports shadows it
        if multi and (name in IGNORABLE_METHODS or parts[0] in BUILTINS):
            return                      # `console.log`, `Set.has`, `JSON.parse`, `Promise.resolve`: library calls
        if multi and self._is_external(file, parts[0]):
            return
        kinds = ("method",) if multi else ("function", "class")
        candidates = [s for s in self.by_name.get(name, []) if s.kind in kinds]
        if not candidates:
            # `x.method()` with no such method anywhere in the project is a library call, not a graph gap.
            if record_missing and not multi and not quiet and name not in BUILTINS:
                self.unres.append(Unresolved(e.src, e.target, e.line, [], "no-definition", file))
            return
        if len(candidates) == 1:
            conf = "low" if multi else "med"
            self.out.append(Resolved(e.type, e.src, candidates[0].id, e.line, conf, file))
            return
        ranked = sorted(candidates, key=lambda s: (-_shared_prefix(file, s.file), s.id))
        best, second = _shared_prefix(file, ranked[0].file), _shared_prefix(file, ranked[1].file)
        if best > second:
            self.out.append(Resolved(e.type, e.src, ranked[0].id, e.line, "low", file))
        elif not quiet and len(candidates) <= MAX_REPORTED_CANDIDATES:
            self.unres.append(Unresolved(e.src, e.target, e.line, [s.id for s in ranked[:8]], "ambiguous", file))

    def _is_external(self, file: str, head: str) -> bool:
        b = self.imports.get(file)
        if not b or head not in b.by_alias:
            return False
        module, name, level = b.by_alias[head]
        return not self.module_files(file, module, level) and not (name and self.module_files(file, f"{module}.{name}", level))


@dataclass
class _ModuleRef:
    module: str
    level: int
    files: list[str]
    importer: str


def _shared_prefix(a: str, b: str) -> int:
    n = 0
    for x, y in zip(posixpath.dirname(a).split("/"), posixpath.dirname(b).split("/")):
        if x != y:
            break
        n += 1
    return n


def _return_type(text: str) -> str | None:
    """`"Atlas"`, `Optional[Atlas]`, `Atlas | None`, `pkg.Atlas` -> the class name; None for generics and builtins."""
    t = text.strip().strip("\"'")
    m = re.fullmatch(r"(?:typing\.)?Optional\[(.+)\]", t)
    if m:
        t = m.group(1).strip().strip("\"'")
    t = re.sub(r"\s*\|\s*None$|^None\s*\|\s*", "", t)
    return t if re.fullmatch(r"[A-Za-z_][\w.]*", t) and t not in BUILTINS else None
