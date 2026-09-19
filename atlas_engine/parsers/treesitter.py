"""Tree-sitter extraction for JavaScript, TypeScript (+TSX), Java and Go.

Same output contract as python_ast.py: symbols plus raw edges (names as written). Receiver types are
inferred from declarations so `repo.save()` resolves to `Repository.save`. Cross-file linking is done by
resolve.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

from ..model import FileParse, ImportBinding, RawEdge, Symbol

MAX_SIGNATURE = 160
MAX_SUMMARY = 100
SUPPORTED = ("javascript", "typescript", "tsx", "java", "go")


@lru_cache(maxsize=None)
def _parser(lang: str):
    from tree_sitter_language_pack import get_parser
    return get_parser(lang)


@lru_cache(maxsize=1)
def available() -> set[str]:
    out = set()
    for lang in SUPPORTED:
        try:
            _parser(lang)
            out.add(lang)
        except Exception:
            continue
    return out


def parse(path: str, lang: str, source: str, sha: str) -> FileParse | None:
    if lang not in available():
        return None
    data = source.encode("utf-8", "replace")
    fp = FileParse(path=path, lang=lang, sha=sha, line_count=len(source.splitlines()))
    tree = _parser(lang).parse(data)
    if tree.root_node.has_error:
        fp.error = "tree-sitter reported syntax errors; extraction is best-effort"
    walker = {"java": _Java, "go": _Go}.get(lang, _JS)(fp, data)
    walker.run(tree.root_node)
    return fp


@dataclass
class _Scope:
    id: str
    qname: str
    kind: str                       # module | class | function
    self_class: str = ""
    class_scope: "_Scope | None" = None
    var_types: dict[str, str] = field(default_factory=dict)
    attr_types: dict[str, str] = field(default_factory=dict)
    locals: set[str] = field(default_factory=set)
    used: bool = False


def _base_type(text: str) -> str | None:
    """`List<Foo>[]` -> "List", `*pkg.Foo` -> "pkg.Foo"; None when it is not a plain type name."""
    t = re.sub(r"<.*>", "", text).replace("[]", "").replace("*", "").lstrip(": ").strip()
    return t if re.fullmatch(r"[A-Za-z_$][\w$.]*", t) else None


class _Base:
    def __init__(self, fp: FileParse, src: bytes):
        self.fp = fp
        self.src = src
        self.seen_ids: dict[str, int] = {}
        self.stack: list[_Scope] = [_Scope(id=f"{fp.path}::<module>", qname="<module>", kind="module")]

    # -- plumbing ------------------------------------------------------------------------------

    @property
    def scope(self) -> _Scope:
        return self.stack[-1]

    def text(self, node) -> str:
        return self.src[node.start_byte:node.end_byte].decode("utf-8", "replace")

    def field(self, node, name: str):
        return node.child_by_field_name(name)

    def run(self, root) -> None:
        self.visit(root)
        mod = self.stack[0]
        if mod.used:
            self.fp.symbols.insert(0, Symbol(
                id=mod.id, file=self.fp.path, kind="module", name="<module>", qname="<module>",
                start=1, end=max(1, self.fp.line_count), summary="Top-level statements"))

    def visit(self, node) -> None:
        handler = getattr(self, "on_" + node.type, None)
        if handler and handler(node):
            return
        for child in node.children:
            self.visit(child)

    def _ctx(self) -> str:
        s = self.scope
        return s.qname if s.kind == "class" else s.self_class

    def edge(self, type_: str, target: str, line: int, *, inferred: bool = False, local: bool = False) -> None:
        self.scope.used = True
        self.fp.edges.append(RawEdge(type_, self.scope.id, target, line, inferred=inferred, local=local, ctx=self._ctx()))

    def define(self, node, kind: str, name: str, body=None, qname: str | None = None) -> Symbol:
        parent = self.scope
        qname = qname or (name if parent.kind == "module" else f"{parent.qname}.{name}")
        base_id = f"{self.fp.path}::{qname}"
        count = self.seen_ids.get(base_id, 0) + 1
        self.seen_ids[base_id] = count
        if count > 1:
            qname = f"{qname}#{count}"
        span = node.parent if node.parent is not None and node.parent.type == "export_statement" else node
        header = self.src[span.start_byte:(body.start_byte if body is not None and body.start_byte > span.start_byte else span.end_byte)]
        signature = re.sub(r"\s+", " ", header.decode("utf-8", "replace")).strip().rstrip("{").strip()[:MAX_SIGNATURE]
        sym = Symbol(
            id=f"{self.fp.path}::{qname}", file=self.fp.path, kind=kind, name=name, qname=qname,
            start=span.start_point[0] + 1, end=span.end_point[0] + 1, signature=signature,
            summary=self._leading_comment(span),
        )
        self.fp.symbols.append(sym)
        return sym

    def _leading_comment(self, node) -> str:
        prev = node.prev_sibling
        if prev is None or "comment" not in prev.type or prev.end_point[0] < node.start_point[0] - 1:
            return ""
        for raw in self.text(prev).splitlines():
            cleaned = re.sub(r"^\s*(/\*+|\*+/?|//+|#)\s?", "", raw).strip().rstrip("*/").strip()
            if cleaned and not cleaned.startswith("@"):
                return cleaned[:MAX_SUMMARY].replace("|", "¦")
        return ""

    def push(self, sym: Symbol, kind: str, **extra) -> _Scope:
        parent = self.scope
        if kind == "function":
            extra.setdefault("self_class", parent.qname if parent.kind == "class" else parent.self_class)
            extra.setdefault("class_scope", parent if parent.kind == "class" else parent.class_scope)
        scope = _Scope(id=sym.id, qname=sym.qname, kind=kind, **extra)
        self.stack.append(scope)
        return scope

    def pop(self) -> None:
        self.stack.pop()

    def lookup_type(self, head: str) -> str | None:
        for scope in reversed(self.stack):
            if scope.kind != "class" and head in scope.var_types:
                return scope.var_types[head]
        return None

    def is_local(self, head: str) -> bool:
        return any(s.kind == "function" and head in s.locals for s in self.stack)

    def anonymous_scope(self, params_binder, body) -> None:
        """A callback or lambda: calls inside still belong to the enclosing symbol, but its parameters are local names."""
        parent = self.scope
        pseudo = _Scope(id=parent.id, qname=parent.qname, kind="function", self_class=parent.self_class,
                        class_scope=parent.class_scope or (parent if parent.kind == "class" else None))
        self.stack.append(pseudo)
        params_binder()
        if body is not None:
            self.visit(body)
        self.stack.pop()
        if pseudo.used:
            parent.used = True

    def qualify(self, parts: list[str]) -> tuple[str, bool, bool]:
        head = parts[0]
        if len(parts) > 1:
            t = self.lookup_type(head)
            if t and t.startswith("@ret:"):
                return f"{t}:{'.'.join(parts[1:])}", True, False       # typed later from the callee's return annotation
            if t:
                return ".".join([t, *parts[1:]]), True, False
            if head == "self" and len(parts) >= 3:
                cs = self.scope.class_scope
                t = cs.attr_types.get(parts[1]) if cs else None
                if t:
                    return ".".join([t, *parts[2:]]), True, False
            elif not self.is_local(head):
                cs = self.scope.class_scope     # Java: `repo.save()` means `this.repo.save()`
                t = cs.attr_types.get(head) if cs else None
                if t:
                    return ".".join([t, *parts[1:]]), True, False
        return ".".join(parts), False, self.is_local(head)

    def call_edge(self, parts: list[str] | None, line: int, fallback_name: str | None = None) -> None:
        if parts:
            target, inferred, local = self.qualify(parts)
            self.edge("CALLS", target, line, inferred=inferred, local=local)
        elif fallback_name:
            self.edge("CALLS", f"*.{fallback_name}", line)

    def bind_param(self, name: str, type_text: str | None) -> None:
        if self.scope.kind != "function":
            return
        self.scope.locals.add(name)
        t = _base_type(type_text) if type_text else None
        if t and t[:1].isupper():
            self.scope.var_types[name] = t


# ------------------------------------------------------------------------------------ JS / TS


class _JS(_Base):
    FUNC_VALUES = ("arrow_function", "function_expression", "function", "generator_function")

    def chain(self, n) -> list[str] | None:
        t = n.type
        if t in ("identifier", "property_identifier", "type_identifier"):
            return [self.text(n)]
        if t == "this":
            return ["self"]
        if t == "super":
            return ["super"]
        if t == "member_expression":
            obj, prop = self.field(n, "object"), self.field(n, "property")
            base = self.chain(obj) if obj is not None else None
            return base + [self.text(prop)] if base and prop is not None else None
        if t in ("parenthesized_expression", "non_null_expression") and n.named_child_count:
            return self.chain(n.named_children[0])
        return None

    def params(self, node) -> None:
        if node is None:
            return
        for child in node.named_children:
            name = type_text = None
            if child.type == "identifier":
                name = self.text(child)
            elif child.type in ("required_parameter", "optional_parameter"):
                pat = self.field(child, "pattern")
                if pat is not None and pat.type == "identifier":
                    name = self.text(pat)
                ann = self.field(child, "type")
                type_text = self.text(ann) if ann is not None else None
            elif child.type == "assignment_pattern":
                left = self.field(child, "left")
                name = self.text(left) if left is not None and left.type == "identifier" else None
            if name:
                self.bind_param(name, type_text)

    def function_body(self, sym: Symbol, kind_scope: str, params, body) -> None:
        self.push(sym, kind_scope)
        self.params(params)
        if body is not None:
            self.visit(body)
        self.pop()

    def on_function_declaration(self, n) -> bool:
        name = self.field(n, "name")
        if name is None:
            return False
        body = self.field(n, "body")
        sym = self.define(n, "function", self.text(name), body)
        self.function_body(sym, "function", self.field(n, "parameters"), body)
        return True

    on_generator_function_declaration = on_function_declaration

    def on_arrow_function(self, n) -> bool:
        params = self.field(n, "parameters") or self.field(n, "parameter")
        self.anonymous_scope(lambda: self.params(params) if params is not None and params.type == "formal_parameters" else self._single_param(params), self.field(n, "body"))
        return True

    def _single_param(self, node) -> None:
        if node is not None and node.type == "identifier":
            self.bind_param(self.text(node), None)

    on_function_expression = on_function = on_generator_function = on_arrow_function

    def on_class_declaration(self, n) -> bool:
        name = self.field(n, "name")
        if name is None:
            return False
        body = self.field(n, "body")
        sym = self.define(n, "class", self.text(name), body)
        for child in n.children:
            if child.type == "class_heritage":
                self.heritage(sym, child)
        scope = self.push(sym, "class")
        scope.attr_types = {}
        if body is not None:
            self.visit(body)
        self.pop()
        return True

    def heritage(self, sym: Symbol, node) -> None:
        def add(n):
            parts = self.chain(n)
            if parts:
                self.fp.edges.append(RawEdge("EXTENDS", sym.id, ".".join(parts), n.start_point[0] + 1, ctx=self._ctx()))

        for c in node.named_children:
            if c.type in ("identifier", "member_expression"):
                add(c)
            elif c.type == "extends_clause":
                value = self.field(c, "value")
                if value is not None:
                    add(value)
            elif c.type == "implements_clause":
                for t in c.named_children:
                    add(t)

    def on_interface_declaration(self, n) -> bool:
        name = self.field(n, "name")
        if name is None:
            return False
        sym = self.define(n, "class", self.text(name), self.field(n, "body"))
        sym.summary = sym.summary or "interface"
        for c in n.named_children:
            if c.type == "extends_type_clause":
                for t in c.named_children:
                    parts = self.chain(t)
                    if parts:
                        self.fp.edges.append(RawEdge("EXTENDS", sym.id, ".".join(parts), t.start_point[0] + 1))
        return True

    def on_enum_declaration(self, n) -> bool:
        name = self.field(n, "name")
        if name is not None:
            self.define(n, "class", self.text(name), self.field(n, "body"))
        return True

    def on_method_definition(self, n) -> bool:
        name = self.field(n, "name")
        if name is None:
            return False
        body = self.field(n, "body")
        sym = self.define(n, "method", self.text(name), body)
        self.function_body(sym, "function", self.field(n, "parameters"), body)
        return True

    def on_public_field_definition(self, n) -> bool:
        name, value = self.field(n, "name"), self.field(n, "value")
        if name is not None and value is not None and value.type in self.FUNC_VALUES:
            body = self.field(value, "body")
            sym = self.define(n, "method", self.text(name), body)
            self.function_body(sym, "function", self.field(value, "parameters"), body)
            return True
        return False

    on_field_definition = on_public_field_definition

    def on_variable_declarator(self, n) -> bool:
        name, value = self.field(n, "name"), self.field(n, "value")
        if name is None:
            return False
        if value is not None and name.type == "identifier" and value.type in self.FUNC_VALUES:
            body = self.field(value, "body")
            outer = n.parent if n.parent is not None and n.parent.type in ("lexical_declaration", "variable_declaration") else n
            sym = self.define(outer, "method" if self.scope.kind == "class" else "function", self.text(name), body)
            self.function_body(sym, "function", self.field(value, "parameters") or self.field(value, "parameter"), body)
            return True
        if value is not None and name.type == "identifier" and value.type == "call_expression":
            inner = self._wrapped_function(value, self.text(name))
            if inner is not None:
                body = self.field(inner, "body")
                outer = n.parent if n.parent is not None and n.parent.type in ("lexical_declaration", "variable_declaration") else n
                sym = self.define(outer, "method" if self.scope.kind == "class" else "function", self.text(name), body)
                self.function_body(sym, "function", self.field(inner, "parameters") or self.field(inner, "parameter"), body)
                return True
        if value is not None and value.type == "call_expression":
            fn = self.field(value, "function")
            if fn is not None and fn.type == "identifier" and self.text(fn) == "require":
                spec = self.string_arg(value)
                if spec:
                    self.fp.edges.append(RawEdge("IMPORTS", self.fp.path, spec, n.start_point[0] + 1))
                    if name.type == "identifier":
                        self.fp.imports.append(ImportBinding(self.text(name), spec, None, 0, n.start_point[0] + 1))
                    elif name.type == "object_pattern":
                        for c in name.named_children:
                            if c.type == "shorthand_property_identifier_pattern":
                                self.fp.imports.append(ImportBinding(self.text(c), spec, self.text(c), 0, n.start_point[0] + 1))
                    return True
        if name.type == "identifier":
            if self.scope.kind == "function":
                self.scope.locals.add(self.text(name))
            ann = next((c for c in n.children if c.type == "type_annotation"), None)
            t = _base_type(self.text(ann)) if ann is not None else None
            call = value
            while call is not None and call.type in ("await_expression", "parenthesized_expression", "as_expression", "non_null_expression") and call.named_child_count:
                call = call.named_children[0]
            if t and t[:1].isupper():
                self.scope.var_types[self.text(name)] = t               # an explicit annotation wins
            elif call is not None and call.type == "new_expression":
                ctor = self.field(call, "constructor")
                parts = self.chain(ctor) if ctor is not None else None
                if parts:
                    self.scope.var_types[self.text(name)] = ".".join(parts)
            elif call is not None and call.type == "call_expression":
                fn = self.field(call, "function")
                parts = self.chain(fn) if fn is not None else None
                if parts and parts[0] != "require":
                    self.scope.var_types[self.text(name)] = "@ret:" + ".".join(parts)   # x = make()  ->  type from make's return
        return False

    WRAPPERS = {"forwardRef", "memo", "observer", "useCallback", "debounce", "throttle", "connect", "withRouter", "lazy"}

    def _wrapped_function(self, call, var_name: str, depth: int = 0):
        """`const Button = React.forwardRef((props, ref) => ...)`: the function inside a wrapper call.

        Only for components (Capitalised names) or well-known wrappers, so `const x = compute(() => ...)` stays a value.
        """
        fn = self.field(call, "function")
        callee = self.chain(fn) if fn is not None else None
        known = bool(callee) and callee[-1] in self.WRAPPERS
        if not (var_name[:1].isupper() or known) or depth > 3:
            return None
        args = self.field(call, "arguments")
        for a in (args.named_children if args is not None else []):
            if a.type in self.FUNC_VALUES:
                return a
            if a.type == "call_expression":
                inner = self._wrapped_function(a, var_name, depth + 1)
                if inner is not None:
                    return inner
        return None

    def string_arg(self, call) -> str | None:
        args = self.field(call, "arguments")
        if args is not None and args.named_child_count and args.named_children[0].type == "string":
            return self.text(args.named_children[0]).strip("'\"`")
        return None

    def on_call_expression(self, n) -> bool:
        fn = self.field(n, "function")
        line = n.start_point[0] + 1
        if fn is not None:
            if fn.type == "identifier" and self.text(fn) == "require" and self.string_arg(n):
                self.fp.edges.append(RawEdge("IMPORTS", self.fp.path, self.string_arg(n) or "", line))
                return False
            parts = self.chain(fn)
            prop = self.field(fn, "property") if fn.type == "member_expression" else None
            self.call_edge(parts, fn.end_point[0] + 1, self.text(prop) if prop is not None else None)
        args = self.field(n, "arguments")
        if args is not None:
            for a in args.named_children:
                parts = self.chain(a)
                if parts:
                    target, inferred, local = self.qualify(parts)
                    if not local:
                        self.edge("REFERENCES", target, a.start_point[0] + 1, inferred=inferred)
        return False

    def on_new_expression(self, n) -> bool:
        ctor = self.field(n, "constructor")
        if ctor is not None:
            self.call_edge(self.chain(ctor), n.start_point[0] + 1)
        return False

    def on_jsx_self_closing_element(self, n) -> bool:
        name = self.field(n, "name")
        if name is not None and self.text(name)[:1].isupper():
            self.call_edge(self.chain(name), n.start_point[0] + 1)
        return False

    on_jsx_opening_element = on_jsx_self_closing_element

    def on_import_statement(self, n) -> bool:
        source = self.field(n, "source")
        if source is None:
            return True
        spec = self.text(source).strip("'\"")
        line = n.start_point[0] + 1
        self.fp.edges.append(RawEdge("IMPORTS", self.fp.path, spec, line))
        for clause in n.named_children:
            if clause.type != "import_clause":
                continue
            for part in clause.named_children:
                if part.type == "identifier":
                    self.fp.imports.append(ImportBinding(self.text(part), spec, "default", 0, line))
                elif part.type == "namespace_import":
                    ident = next((c for c in part.named_children if c.type == "identifier"), None)
                    if ident is not None:
                        self.fp.imports.append(ImportBinding(self.text(ident), spec, None, 0, line))
                elif part.type == "named_imports":
                    for spec_node in part.named_children:
                        orig, alias = self.field(spec_node, "name"), self.field(spec_node, "alias")
                        if orig is not None:
                            self.fp.imports.append(ImportBinding(self.text(alias or orig), spec, self.text(orig), 0, line))
        return True


# ------------------------------------------------------------------------------------ Java


class _Java(_Base):
    CLASS_LIKE = ("class_declaration", "interface_declaration", "enum_declaration", "record_declaration")

    def chain(self, n) -> list[str] | None:
        t = n.type
        if t == "identifier":
            return [self.text(n)]
        if t == "this":
            return ["self"]
        if t == "super":
            return ["super"]
        if t == "field_access":
            obj, fld = self.field(n, "object"), self.field(n, "field")
            base = self.chain(obj) if obj is not None else None
            return base + [self.text(fld)] if base and fld is not None else None
        if t == "scoped_identifier":
            return self.text(n).split(".")
        return None

    def type_names(self, node) -> list[str]:
        names = []
        for c in node.named_children:
            if c.type in ("type_identifier", "scoped_type_identifier", "generic_type"):
                t = _base_type(self.text(c))
                if t:
                    names.append(t)
            elif c.type in ("type_list", "super_interfaces", "extends_interfaces", "superclass"):
                names += self.type_names(c)
        return names

    def on_class_declaration(self, n) -> bool:
        name = self.field(n, "name")
        if name is None:
            return False
        body = self.field(n, "body")
        sym = self.define(n, "class", self.text(name), body)
        if n.type == "interface_declaration":
            sym.summary = sym.summary or "interface"
        for key in ("superclass", "interfaces"):
            part = self.field(n, key)
            if part is not None:
                for t in self.type_names(part):
                    self.fp.edges.append(RawEdge("EXTENDS", sym.id, t, n.start_point[0] + 1, ctx=self._ctx()))
        for c in n.named_children:
            if c.type == "extends_interfaces":
                for t in self.type_names(c):
                    self.fp.edges.append(RawEdge("EXTENDS", sym.id, t, n.start_point[0] + 1, ctx=self._ctx()))
        scope = self.push(sym, "class")
        if body is not None:
            for member in body.named_children:          # fields first, so methods can use their types
                if member.type == "field_declaration":
                    ftype = self.field(member, "type")
                    t = _base_type(self.text(ftype)) if ftype is not None else None
                    for decl in member.named_children:
                        dname = self.field(decl, "name") if decl.type == "variable_declarator" else None
                        if dname is not None and t and t[:1].isupper():
                            scope.attr_types[self.text(dname)] = t
            self.visit(body)
        self.pop()
        return True

    on_interface_declaration = on_enum_declaration = on_record_declaration = on_class_declaration

    def on_method_declaration(self, n) -> bool:
        name = self.field(n, "name")
        if name is None:
            return False
        body = self.field(n, "body")
        sym = self.define(n, "method", self.text(name), body)
        self.push(sym, "function")
        params = self.field(n, "parameters")
        if params is not None:
            for p in params.named_children:
                pname, ptype = self.field(p, "name"), self.field(p, "type")
                if pname is not None:
                    self.bind_param(self.text(pname), self.text(ptype) if ptype is not None else None)
        if body is not None:
            self.visit(body)
        self.pop()
        return True

    on_constructor_declaration = on_method_declaration

    def on_method_invocation(self, n) -> bool:
        obj, name = self.field(n, "object"), self.field(n, "name")
        if name is None:
            return False
        method = self.text(name)
        line = name.start_point[0] + 1
        if obj is None:
            parts = ["self", method] if self._ctx() else [method]
            self.call_edge(parts, line)
        elif obj.type == "object_creation_expression" and self.field(obj, "type") is not None:
            made = _base_type(self.text(self.field(obj, "type")))
            if made:
                self.edge("CALLS", f"{made}.{method}", line, inferred=True)
            else:
                self.call_edge(None, line, method)
        else:
            base = self.chain(obj)
            self.call_edge(base + [method] if base else None, line, method)
        return False

    def on_lambda_expression(self, n) -> bool:
        def bind():
            p = self.field(n, "parameters")
            if p is None:
                return
            for c in ([p] if p.type == "identifier" else p.named_children):
                ident = c if c.type == "identifier" else self.field(c, "name")
                if ident is not None:
                    self.bind_param(self.text(ident), None)
        self.anonymous_scope(bind, self.field(n, "body"))
        return True

    def on_object_creation_expression(self, n) -> bool:
        t = self.field(n, "type")
        name = _base_type(self.text(t)) if t is not None else None
        if name:
            self.call_edge(name.split("."), n.start_point[0] + 1)
        return False

    def on_method_reference(self, n) -> bool:
        parts = [p for p in self.text(n).split("::") if p]
        if len(parts) == 2 and all(re.fullmatch(r"[\w$.]+", p) for p in parts):
            target, inferred, local = self.qualify([*parts[0].split("."), parts[1]])
            if not local:
                self.edge("REFERENCES", target, n.start_point[0] + 1, inferred=inferred)
        return False

    def on_local_variable_declaration(self, n) -> bool:
        vtype = self.field(n, "type")
        t = _base_type(self.text(vtype)) if vtype is not None else None
        for decl in n.named_children:
            dname = self.field(decl, "name") if decl.type == "variable_declarator" else None
            if dname is not None and self.scope.kind == "function":
                self.scope.locals.add(self.text(dname))
                if t and t != "var" and t[:1].isupper():
                    self.scope.var_types[self.text(dname)] = t
        return False

    def on_import_declaration(self, n) -> bool:
        line = n.start_point[0] + 1
        ident = next((c for c in n.named_children if c.type in ("scoped_identifier", "identifier")), None)
        if ident is None:
            return True
        full = self.text(ident)
        if any(c.type == "asterisk" for c in n.children):
            self.fp.imports.append(ImportBinding("*", full, "*", 0, line))
            self.fp.edges.append(RawEdge("IMPORTS", self.fp.path, full, line))
        else:
            module, _, name = full.rpartition(".")
            self.fp.imports.append(ImportBinding(name, module, name, 0, line))
            self.fp.edges.append(RawEdge("IMPORTS", self.fp.path, module, line, aux=name))
        return True


# ------------------------------------------------------------------------------------ Go


class _Go(_Base):
    def chain(self, n) -> list[str] | None:
        t = n.type
        if t == "identifier":
            return [self.text(n)]
        if t == "selector_expression":
            operand, fld = self.field(n, "operand"), self.field(n, "field")
            base = self.chain(operand) if operand is not None else None
            return base + [self.text(fld)] if base and fld is not None else None
        if t in ("parenthesized_expression",) and n.named_child_count:
            return self.chain(n.named_children[0])
        return None

    def bind_params(self, params) -> None:
        if params is None:
            return
        for decl in params.named_children:
            ptype = self.field(decl, "type")
            for c in decl.named_children:
                if c.type == "identifier":
                    self.bind_param(self.text(c), self.text(ptype) if ptype is not None else None)

    def on_function_declaration(self, n) -> bool:
        name = self.field(n, "name")
        if name is None:
            return False
        body = self.field(n, "body")
        sym = self.define(n, "function", self.text(name), body)
        self.push(sym, "function", self_class="")
        self.bind_params(self.field(n, "parameters"))
        if body is not None:
            self.visit(body)
        self.pop()
        return True

    def on_method_declaration(self, n) -> bool:
        name, receiver = self.field(n, "name"), self.field(n, "receiver")
        if name is None:
            return False
        recv_type = recv_name = None
        if receiver is not None and receiver.named_child_count:
            decl = receiver.named_children[0]
            rtype = self.field(decl, "type")
            recv_type = _base_type(self.text(rtype)) if rtype is not None else None
            rname = next((c for c in decl.named_children if c.type == "identifier"), None)
            recv_name = self.text(rname) if rname is not None else None
        body = self.field(n, "body")
        qname = f"{recv_type}.{self.text(name)}" if recv_type else None
        sym = self.define(n, "method", self.text(name), body, qname=qname)
        self.push(sym, "function", self_class=recv_type or "")
        if recv_name and recv_type:
            self.scope.locals.add(recv_name)
            self.scope.var_types[recv_name] = recv_type
        self.bind_params(self.field(n, "parameters"))
        if body is not None:
            self.visit(body)
        self.pop()
        return True

    def on_type_declaration(self, n) -> bool:
        for spec in n.named_children:
            if spec.type != "type_spec":
                continue
            name, typ = self.field(spec, "name"), self.field(spec, "type")
            if name is None:
                continue
            sym = self.define(spec, "class", self.text(name), None)
            if typ is not None and typ.type == "interface_type":
                sym.summary = sym.summary or "interface"
            if typ is not None and typ.type == "struct_type":
                for fld in typ.named_children:
                    for decl in fld.named_children:
                        for member in decl.named_children:
                            if member.type == "field_declaration" and self.field(member, "name") is None:
                                emb = self.field(member, "type")
                                t = _base_type(self.text(emb)) if emb is not None else None
                                if t:
                                    self.fp.edges.append(RawEdge("EXTENDS", sym.id, t, member.start_point[0] + 1))
        return True

    def on_func_literal(self, n) -> bool:
        self.anonymous_scope(lambda: self.bind_params(self.field(n, "parameters")), self.field(n, "body"))
        return True

    def on_call_expression(self, n) -> bool:
        fn = self.field(n, "function")
        if fn is not None:
            prop = self.field(fn, "field") if fn.type == "selector_expression" else None
            self.call_edge(self.chain(fn), fn.end_point[0] + 1, self.text(prop) if prop is not None else None)
        return False

    def on_import_spec(self, n) -> bool:
        path, alias = self.field(n, "path"), self.field(n, "name")
        if path is None:
            return True
        module = self.text(path).strip('"`')
        bound = self.text(alias) if alias is not None else module.rsplit("/", 1)[-1]
        line = n.start_point[0] + 1
        if bound not in ("_", "."):
            self.fp.imports.append(ImportBinding(bound, module, None, 0, line))
        self.fp.edges.append(RawEdge("IMPORTS", self.fp.path, module, line))
        return True

    def on_short_var_declaration(self, n) -> bool:
        left, right = self.field(n, "left"), self.field(n, "right")
        if left is not None and right is not None and self.scope.kind == "function":
            names = [c for c in left.named_children if c.type == "identifier"]
            values = right.named_children
            for i, ident in enumerate(names):
                self.scope.locals.add(self.text(ident))
                value = values[i] if i < len(values) else None
                if value is not None and value.type == "unary_expression" and value.named_child_count:
                    value = value.named_children[-1]
                if value is not None and value.type == "composite_literal":
                    t = self.field(value, "type")
                    base = _base_type(self.text(t)) if t is not None else None
                    if base and base[:1].isupper():
                        self.scope.var_types[self.text(ident)] = base
        return False
