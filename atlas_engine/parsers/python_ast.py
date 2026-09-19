"""Exact Python extraction built on the standard-library `ast` module.

Produces symbols plus *raw* edges (names as written). Cross-file resolution happens later in resolve.py.
When a file has a syntax error (common in half-finished practice code) we fall back to a tolerant
line scanner that still recovers class/def symbols so the file is not invisible to the graph.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field

from ..model import FileParse, ImportBinding, RawEdge, Symbol

MAX_SIGNATURE = 160
MAX_SUMMARY = 100

_DEF_RE = re.compile(r"^(\s*)(async\s+def|def|class)\s+([A-Za-z_]\w*)")


def parse_python(path: str, source: str, sha: str) -> FileParse:
    lines = source.splitlines()
    fp = FileParse(path=path, lang="python", sha=sha, line_count=len(lines))
    try:
        tree = ast.parse(source, filename=path)
    except (SyntaxError, ValueError, RecursionError) as exc:
        where = getattr(exc, "lineno", None)
        fp.error = f"{type(exc).__name__}{f' line {where}' if where else ''}: {getattr(exc, 'msg', exc)}"
        _scan_tolerant(fp, lines)
        return fp
    _Extractor(fp, lines).run(tree)
    return fp


# --------------------------------------------------------------------------------------- helpers


def _chain(node: ast.AST) -> list[str] | None:
    """`a.b.c` -> ["a", "b", "c"]; None when the expression is not a plain dotted name."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return parts[::-1]
    return None


def _dotted(node: ast.AST) -> str | None:
    parts = _chain(node)
    return ".".join(parts) if parts else None


def _annotation_type(node: ast.AST | None) -> str | None:
    """Best-effort class name from an annotation: Foo, pkg.Foo, "Foo", Optional[Foo], Foo | None."""
    if node is None:
        return None
    if isinstance(node, (ast.Name, ast.Attribute)):
        return _dotted(node)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        text = node.value.strip()
        return text if re.fullmatch(r"[A-Za-z_][\w.]*", text) else None
    if isinstance(node, ast.Subscript) and _dotted(node.value) in ("Optional", "typing.Optional"):
        return _annotation_type(node.slice)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        for side in (node.left, node.right):
            if isinstance(side, ast.Constant) and side.value is None:
                continue
            found = _annotation_type(side)
            if found:
                return found
    return None


def _looks_like_class(dotted: str) -> bool:
    return dotted.rsplit(".", 1)[-1][:1].isupper()


def _param_types(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, str]:
    types: dict[str, str] = {}
    args = fn.args
    for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]:
        t = _annotation_type(a.annotation)
        if t:
            types[a.arg] = t
    return types


def _collect_locals(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Names bound inside a function (parameters and assignments). Nested def/class names are excluded on purpose."""
    args = fn.args
    names = {a.arg for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]}
    for a in (args.vararg, args.kwarg):
        if a:
            names.add(a.arg)
    stack: list[ast.AST] = list(fn.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        stack.extend(ast.iter_child_nodes(node))
    names.discard("self")
    names.discard("cls")
    return names


def _attr_types(cls: ast.ClassDef) -> dict[str, str]:
    """Instance attribute types from `self.x = Foo()` / `self.x: Foo` inside methods, and class-level `x: Foo`."""
    types: dict[str, str] = {}
    for stmt in cls.body:
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            t = _annotation_type(stmt.annotation)
            if t:
                types[stmt.target.id] = t
        if not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(stmt):
            target = value = annotation = None
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target, value = node.targets[0], node.value
            elif isinstance(node, ast.AnnAssign):
                target, value, annotation = node.target, node.value, node.annotation
            if not (isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == "self"):
                continue
            t = _annotation_type(annotation)
            if not t and isinstance(value, ast.Call):
                callee = _dotted(value.func)
                if callee and _looks_like_class(callee):
                    t = callee
            if t:
                types.setdefault(target.attr, t)
    return types


@dataclass
class _Scope:
    id: str
    qname: str
    kind: str                       # module | class | function
    self_class: str = ""            # qualified name of the class that `self`/`cls` refers to
    class_scope: "_Scope | None" = None
    var_types: dict[str, str] = field(default_factory=dict)
    attr_types: dict[str, str] = field(default_factory=dict)
    locals: set[str] = field(default_factory=set)
    used: bool = False


# --------------------------------------------------------------------------------------- extractor


class _Extractor(ast.NodeVisitor):
    def __init__(self, fp: FileParse, lines: list[str]):
        self.fp = fp
        self.lines = lines
        self.seen_ids: dict[str, int] = {}
        self.main_guard = False
        self.stack: list[_Scope] = [_Scope(id=f"{fp.path}::<module>", qname="<module>", kind="module")]

    @property
    def scope(self) -> _Scope:
        return self.stack[-1]

    def run(self, tree: ast.Module) -> None:
        for stmt in tree.body:
            self.visit(stmt)
        mod = self.stack[0]
        if mod.used or self.main_guard:
            summary = "Script entry point (if __name__ == '__main__')" if self.main_guard else "Top-level statements"
            self.fp.symbols.insert(0, Symbol(
                id=mod.id, file=self.fp.path, kind="module", name="<module>", qname="<module>",
                start=1, end=max(1, self.fp.line_count), summary=summary,
            ))

    # -- symbols -------------------------------------------------------------------------------

    def _new_symbol(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef, kind: str) -> Symbol:
        parent = self.scope
        qname = node.name if parent.kind == "module" else f"{parent.qname}.{node.name}"
        base_id = f"{self.fp.path}::{qname}"
        count = self.seen_ids.get(base_id, 0) + 1
        self.seen_ids[base_id] = count
        if count > 1:
            qname = f"{qname}#{count}"
        start = min([node.lineno, *[d.lineno for d in node.decorator_list]])
        sym = Symbol(
            id=f"{self.fp.path}::{qname}", file=self.fp.path, kind=kind, name=node.name, qname=qname,
            start=start, end=node.end_lineno or node.lineno,
            signature=self._signature(node), summary=_first_line(ast.get_docstring(node)),
        )
        self.fp.symbols.append(sym)
        return sym

    def _signature(self, node: ast.AST) -> str:
        first = node.lineno  # type: ignore[attr-defined]
        body = getattr(node, "body", [])
        last = body[0].lineno - 1 if body and body[0].lineno > first else first
        text = " ".join(s.strip() for s in self.lines[first - 1:max(first, last)] if not s.strip().startswith("#"))
        return text[:MAX_SIGNATURE]

    def _ctx(self) -> str:
        s = self.scope
        return s.qname if s.kind == "class" else s.self_class

    def _edge(self, type_: str, target: str, line: int, *, inferred: bool = False, local: bool = False) -> None:
        self.scope.used = True
        self.fp.edges.append(RawEdge(type_, self.scope.id, target, line, inferred=inferred, local=local, ctx=self._ctx()))

    # -- definitions ---------------------------------------------------------------------------

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        parent = self.scope
        sym = self._new_symbol(node, "method" if parent.kind == "class" else "function")
        for dec in node.decorator_list:
            self._decorator(sym.id, dec)
        for default in [*node.args.defaults, *[d for d in node.args.kw_defaults if d is not None]]:
            self.visit(default)
        scope = _Scope(
            id=sym.id, qname=sym.qname, kind="function",
            self_class=parent.qname if parent.kind == "class" else parent.self_class,
            class_scope=parent if parent.kind == "class" else parent.class_scope,
            var_types=_param_types(node), locals=_collect_locals(node),
        )
        self.stack.append(scope)
        for stmt in node.body:
            self.visit(stmt)
        self.stack.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        parent = self.scope
        sym = self._new_symbol(node, "class")
        for dec in node.decorator_list:
            self._decorator(sym.id, dec)
        for base in node.bases:
            target = _dotted(base)
            if target:
                self.fp.edges.append(RawEdge("EXTENDS", sym.id, target, base.lineno, ctx=self._ctx()))
        for kw in node.keywords:
            self.visit(kw.value)
        scope = _Scope(id=sym.id, qname=sym.qname, kind="class", attr_types=_attr_types(node))
        self.stack.append(scope)
        for stmt in node.body:
            self.visit(stmt)
        self.stack.pop()

    def _decorator(self, sym_id: str, dec: ast.expr) -> None:
        core = dec.func if isinstance(dec, ast.Call) else dec
        target = _dotted(core)
        if target:
            self.fp.edges.append(RawEdge("REFERENCES", sym_id, target, dec.lineno, ctx=self._ctx()))
        if isinstance(dec, ast.Call):
            for arg in [*dec.args, *[k.value for k in dec.keywords]]:
                self.visit(arg)

    # -- imports -------------------------------------------------------------------------------

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            bound = alias.asname or alias.name.split(".")[0]
            module = alias.name if alias.asname else alias.name.split(".")[0]
            self.fp.imports.append(ImportBinding(bound, module, None, 0, node.lineno))
            self.fp.edges.append(RawEdge("IMPORTS", self.fp.path, alias.name, node.lineno))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        for alias in node.names:
            if alias.name == "*":
                self.fp.imports.append(ImportBinding("*", module, "*", node.level, node.lineno))
            else:
                self.fp.imports.append(ImportBinding(alias.asname or alias.name, module, alias.name, node.level, node.lineno))
        self.fp.edges.append(RawEdge(
            "IMPORTS", self.fp.path, module, node.lineno, level=node.level,
            aux=",".join(a.name for a in node.names),
        ))

    # -- calls and references ------------------------------------------------------------------

    def _is_local(self, head: str) -> bool:
        return any(s.kind == "function" and head in s.locals for s in self.stack)

    def _lookup_type(self, head: str) -> str | None:
        for scope in reversed(self.stack):
            if scope.kind != "class" and head in scope.var_types:
                return scope.var_types[head]
        return None

    def _qualify(self, parts: list[str]) -> tuple[str, bool, bool]:
        """Return (target, inferred, local) for a dotted name, substituting inferred receiver types."""
        head = parts[0]
        if len(parts) > 1:
            t = self._lookup_type(head)
            if t and t.startswith("@ret:"):
                return f"{t}:{'.'.join(parts[1:])}", True, False   # typed later from the callee's return annotation
            if t:
                return ".".join([t, *parts[1:]]), True, False
            if head in ("self", "cls") and len(parts) >= 3:
                cs = self.scope.class_scope
                t = cs.attr_types.get(parts[1]) if cs else None
                if t:
                    return ".".join([t, *parts[2:]]), True, False
        return ".".join(parts), False, self._is_local(head)

    def _callee(self, func: ast.expr) -> tuple[str | None, bool, bool]:
        if isinstance(func, ast.Name):
            return self._qualify([func.id])
        if isinstance(func, ast.Attribute):
            parts = _chain(func)
            if parts:
                return self._qualify(parts)
            recv = func.value
            if isinstance(recv, ast.Call):
                inner = _dotted(recv.func)
                if inner == "super":
                    return f"super.{func.attr}", False, False
                if inner and _looks_like_class(inner):
                    return f"{inner}.{func.attr}", True, False
            return f"*.{func.attr}", False, False
        return None, False, False

    def visit_Call(self, node: ast.Call) -> None:
        target, inferred, local = self._callee(node.func)
        if target:
            self._edge("CALLS", target, getattr(node.func, "end_lineno", None) or node.lineno, inferred=inferred, local=local)
        self.visit(node.func)
        for arg in node.args:
            self._ref(arg)
            self.visit(arg)
        for kw in node.keywords:
            self._ref(kw.value)
            self.visit(kw.value)

    def _ref(self, node: ast.expr | None) -> None:
        """A function/class used as a value (callback, dict entry, returned). The resolver drops non-symbols."""
        if node is None:
            return
        parts = _chain(node)
        if not parts:
            return
        target, inferred, local = self._qualify(parts)
        if not local:
            self._edge("REFERENCES", target, node.lineno, inferred=inferred)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        """Calls in a lambda belong to the enclosing symbol, but its parameters are local names."""
        parent = self.scope
        a = node.args
        names = {x.arg for x in [*a.posonlyargs, *a.args, *a.kwonlyargs, *[y for y in (a.vararg, a.kwarg) if y]]}
        pseudo = _Scope(id=parent.id, qname=parent.qname, kind="function", self_class=parent.self_class,
                        class_scope=parent.class_scope, locals=names)
        self.stack.append(pseudo)
        self.visit(node.body)
        self.stack.pop()
        if pseudo.used:
            parent.used = True

    def visit_Return(self, node: ast.Return) -> None:
        self._ref(node.value)
        self.generic_visit(node)

    def visit_Dict(self, node: ast.Dict) -> None:
        for value in node.values:
            self._ref(value)
        self.generic_visit(node)

    def _visit_sequence(self, node: ast.List | ast.Tuple | ast.Set) -> None:
        for element in node.elts:
            self._ref(element)
        self.generic_visit(node)

    visit_List = visit_Tuple = visit_Set = _visit_sequence  # type: ignore[assignment]

    def visit_Assign(self, node: ast.Assign) -> None:
        if isinstance(node.value, ast.Call):
            callee = _dotted(node.value.func)
            if callee:
                bound = callee if _looks_like_class(callee) else f"@ret:{callee}"
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        self.scope.var_types[target.id] = bound
        else:
            self._ref(node.value)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        t = _annotation_type(node.annotation)
        if t and isinstance(node.target, ast.Name):
            self.scope.var_types[node.target.id] = t
        self.generic_visit(node)

    def visit_If(self, node: ast.If) -> None:
        test = node.test
        if (
            isinstance(test, ast.Compare) and isinstance(test.left, ast.Name) and test.left.id == "__name__"
            and len(test.comparators) == 1 and isinstance(test.comparators[0], ast.Constant)
            and test.comparators[0].value == "__main__"
        ):
            self.main_guard = True
        self.generic_visit(node)


def _first_line(doc: str | None) -> str:
    if not doc:
        return ""
    first = doc.strip().splitlines()[0].strip()
    return first[:MAX_SUMMARY].replace("|", "¦")


# --------------------------------------------------------------------------------------- fallback


def _scan_tolerant(fp: FileParse, lines: list[str]) -> None:
    """Recover class/def symbols by indentation when the file does not parse. No edges except containment."""
    found: list[dict] = []
    stack: list[tuple[int, str, str]] = []  # (indent, qname, kind)
    seen: dict[str, int] = {}
    for number, line in enumerate(lines, 1):
        m = _DEF_RE.match(line)
        if not m:
            continue
        indent = len(m.group(1).expandtabs(4))
        while stack and stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1] if stack else None
        name = m.group(3)
        qname = f"{parent[1]}.{name}" if parent else name
        is_class = m.group(2) == "class"
        kind = "class" if is_class else ("method" if parent and parent[2] == "class" else "function")
        seen[qname] = seen.get(qname, 0) + 1
        unique = qname if seen[qname] == 1 else f"{qname}#{seen[qname]}"
        found.append({"indent": indent, "start": number, "name": name, "qname": unique, "kind": kind, "sig": line.strip()})
        stack.append((indent, qname, "class" if is_class else "function"))
    for idx, item in enumerate(found):
        end = len(lines)
        for later in found[idx + 1:]:
            if later["indent"] <= item["indent"]:
                end = later["start"] - 1
                break
        while end > item["start"] and not lines[end - 1].strip():
            end -= 1
        fp.symbols.append(Symbol(
            id=f"{fp.path}::{item['qname']}", file=fp.path, kind=item["kind"], name=item["name"], qname=item["qname"],
            start=item["start"], end=end, signature=item["sig"][:MAX_SIGNATURE],
            summary="(recovered from a file with a syntax error)",
        ))
