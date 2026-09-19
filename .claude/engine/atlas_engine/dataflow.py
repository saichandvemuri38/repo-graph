"""Reaching definitions and taint tracking for Python.

Follows a value through a program: where a variable was assigned, which assignments can reach a given use, and whether
data from an untrusted **source** (`input()`, `sys.argv`, `request.args`, an HTTP handler's parameters ...) can reach a
dangerous **sink** (`os.system`, `eval`, `cursor.execute`, `open`, `pickle.loads` ...) without passing a sanitizer.

How it works
  * Each function is analysed on its own with a flow-sensitive walk over its statements. The state maps every variable
    to the definitions that may reach the current line (a *may* analysis: branches are joined, loops run twice).
  * A tainted value carries the list of steps it took, so a finding shows the whole path from source to sink.
  * Function parameters start as symbolic taint. That gives every function a **summary**: which parameters reach which
    sinks, and whether the return value carries a parameter or a source. Summaries are applied at call sites that the
    call graph resolved, so a source in one function can reach a sink in another. They are recomputed until stable.
  * The code graph supplies who is called at each line; everything else here is plain `ast`.

What it does not do (see `.claude/README.md`, Limits): it is not path-sensitive (`if x.isdigit():` does not clean `x`),
follows attributes only as `name.attr` strings, ignores closures and globals across functions, treats unknown library
calls as passing their arguments through (marked "assumed"), and covers Python only.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

MAX_STEPS = 14
MAX_TAINTS = 4
MAX_DEFS = 6
ROUNDS = 5

Step = tuple[int, str, str]          # (line, source code, what happened)


@dataclass(frozen=True)
class Taint:
    origin: str                      # "user input (input())", "parameter `name`", ...
    category: str                    # input | cli | env | http | network | param
    param: int | None                # parameter index when this is symbolic taint, else None
    steps: tuple = ()
    assumed: bool = False            # the path went through a call assumed to keep the value
    cleaned: frozenset = frozenset() # sink kinds this value has been sanitised for

    def then(self, line: int, code: str, note: str, assumed: bool = False) -> "Taint":
        return Taint(self.origin, self.category, self.param, cap(self.steps + ((line, code, note),)), self.assumed or assumed, self.cleaned)

    def clean(self, kinds: frozenset) -> "Taint":
        return Taint(self.origin, self.category, self.param, self.steps, self.assumed, self.cleaned | kinds)


Taints = tuple  # tuple[Taint, ...]


def cap(steps: tuple) -> tuple:
    return steps if len(steps) <= MAX_STEPS else (steps[0], *steps[-(MAX_STEPS - 1):])


def merge(*groups) -> Taints:
    best: dict[tuple, Taint] = {}
    for g in groups:
        for t in g:
            key = (t.origin, t.param, t.cleaned)
            if key not in best or len(t.steps) < len(best[key].steps):
                best[key] = t
    return tuple(sorted(best.values(), key=lambda t: (t.param is not None, len(t.steps))))[:MAX_TAINTS]


@dataclass
class Hit:
    """A sink a parameter reaches inside a function (part of that function's summary)."""
    kind: str
    line: int
    file: str
    sid: str
    steps: tuple
    sink: str


@dataclass
class Summary:
    params: list[str] = field(default_factory=list)
    sink_params: dict[int, list[Hit]] = field(default_factory=dict)
    ret_params: set[int] = field(default_factory=set)
    ret_concrete: tuple = ()

    def signature(self) -> tuple:
        return (tuple(sorted((i, tuple(sorted((h.kind, h.line, h.file) for h in hs))) for i, hs in self.sink_params.items())),
                tuple(sorted(self.ret_params)), tuple(sorted(t.origin for t in self.ret_concrete)))


@dataclass
class Finding:
    kind: str
    title: str
    cwe: str
    severity: str
    file: str                        # where the sink is
    line: int
    function: str                    # symbol id that contains the sink
    source_function: str             # symbol id where the tainted value entered (differs when it crossed a call)
    sink: str
    source: str
    category: str
    steps: tuple
    confidence: str
    fix: str


# ----------------------------------------------------------------------------------------- catalogue

SINK_INFO = {
    "command-exec": ("Command injection", "CWE-78", "HIGH", "Pass a list of arguments (no shell) or quote with `shlex.quote`."),
    "code-exec": ("Code injection", "CWE-95", "HIGH", "Never `eval`/`exec` input; parse it (`ast.literal_eval`, `json.loads`) or use a lookup table."),
    "sql": ("SQL injection", "CWE-89", "HIGH", "Use placeholders: `cur.execute(sql, params)`; never build the query with f-strings, `+` or `%`."),
    "deserialization": ("Unsafe deserialization", "CWE-502", "HIGH", "Do not unpickle untrusted data; use JSON, or `yaml.safe_load`."),
    "path": ("Path traversal", "CWE-22", "MEDIUM", "Resolve the path and check it stays inside an allowed folder; use `os.path.basename` or `secure_filename`."),
    "ssrf": ("Server-side request forgery", "CWE-918", "MEDIUM", "Validate the URL against an allow-list of hosts before requesting it."),
    "import": ("Dynamic import of untrusted name", "CWE-94", "MEDIUM", "Look the module up in a fixed allow-list instead of importing by name."),
    "xss": ("Cross-site scripting", "CWE-79", "MEDIUM", "Escape with `html.escape` / template auto-escaping; avoid `Markup` and `mark_safe` on input."),
}
COMMAND = {"os.system", "os.popen", "commands.getoutput", "subprocess.getoutput", "subprocess.getstatusoutput", "os.startfile"}
SUBPROCESS = {"subprocess.call", "subprocess.run", "subprocess.Popen", "subprocess.check_output", "subprocess.check_call"}
CODE = {"eval", "exec", "compile"}
SQL_METHODS = {"execute", "executemany", "executescript", "raw", "mogrify"}
PATH_ONE = {"open", "io.open", "os.remove", "os.unlink", "os.rmdir", "os.removedirs", "os.mkdir", "os.makedirs", "os.listdir",
            "os.chdir", "os.chmod", "shutil.rmtree", "send_file", "flask.send_file", "send_from_directory"}
PATH_TWO = {"os.rename", "os.replace", "shutil.copy", "shutil.copyfile", "shutil.move", "shutil.copytree"}
DESER = {"pickle.loads", "pickle.load", "cPickle.loads", "marshal.loads", "marshal.load", "dill.loads", "shelve.open", "jsonpickle.decode", "yaml.unsafe_load"}
SSRF = {"requests.get", "requests.post", "requests.put", "requests.delete", "requests.head", "requests.patch", "urllib.request.urlopen",
        "urlopen", "urllib.request.urlretrieve", "httpx.get", "httpx.post"}
IMPORT = {"importlib.import_module", "__import__"}
XSS = {"render_template_string", "flask.render_template_string", "Markup", "markupsafe.Markup", "mark_safe"}
SINK_KWARGS = {"args", "command", "cmd", "query", "sql", "url", "path", "file", "filename", "name", "source", "data", "obj"}

SOURCE_CALLS = {
    "input": ("input", "user input (input())"), "raw_input": ("input", "user input (raw_input())"),
    "sys.stdin.read": ("input", "standard input"), "sys.stdin.readline": ("input", "standard input"), "sys.stdin.readlines": ("input", "standard input"),
    "os.getenv": ("env", "environment variable"), "os.environ.get": ("env", "environment variable"),
    "requests.get": ("network", "network response"), "requests.post": ("network", "network response"), "urlopen": ("network", "network response"),
    "urllib.request.urlopen": ("network", "network response"),
}
SOURCE_METHODS = {"recv": ("network", "network data"), "recvfrom": ("network", "network data"), "parse_args": ("cli", "command-line arguments"),
                  "get_json": ("http", "HTTP request data"), "get_data": ("http", "HTTP request data")}
REQUEST_HEADS = {"request", "flask.request", "req"}
REQUEST_ATTRS = {"args", "form", "values", "json", "data", "files", "cookies", "headers", "view_args", "GET", "POST", "body",
                 "query_params", "path_params", "get_json", "get_data", "environ"}
ROUTE_DECORATORS = {"route", "get", "post", "put", "delete", "patch", "api_route", "websocket", "api_view"}

CLEAN_ALL = {"int", "float", "bool", "len", "isinstance", "type", "id", "ord", "abs", "round", "hash", "uuid.UUID", "UUID", "ipaddress.ip_address"}
CLEAN_KINDS = {
    "shlex.quote": frozenset({"command-exec"}), "pipes.quote": frozenset({"command-exec"}),
    "html.escape": frozenset({"xss"}), "escape": frozenset({"xss"}), "markupsafe.escape": frozenset({"xss"}),
    "os.path.basename": frozenset({"path"}), "secure_filename": frozenset({"path"}), "werkzeug.utils.secure_filename": frozenset({"path"}),
    "urllib.parse.quote": frozenset({"ssrf", "path"}), "quote": frozenset({"ssrf", "path"}), "quote_plus": frozenset({"ssrf", "path"}),
    "json.dumps": frozenset({"xss"}), "ast.literal_eval": frozenset({"code-exec"}),
}
URL_ONLY = {"urllib.request.Request", "Request", "requests.Request", "urllib.request.build_opener"}
MUTATORS = {"append", "extend", "add", "update", "insert", "setdefault", "appendleft", "extendleft", "write", "writelines"}
KEEPS = {  # calls that pass a value through unchanged in meaning: not marked "assumed"
    "str", "bytes", "repr", "list", "tuple", "set", "dict", "sorted", "reversed", "enumerate", "zip", "map", "filter", "min", "max", "sum",
    "json.loads", "base64.b64decode", "base64.b64encode", "os.path.join", "os.path.normpath", "os.path.abspath", "os.path.realpath",
    "os.path.expanduser", "Path", "pathlib.Path", "PurePath", "urllib.parse.unquote", "unquote", "urllib.parse.urlparse", "format",
}
STR_METHODS = {"strip", "lstrip", "rstrip", "lower", "upper", "title", "format", "join", "replace", "split", "rsplit", "splitlines", "encode",
               "decode", "casefold", "capitalize", "zfill", "ljust", "rjust", "center", "partition", "rpartition", "removeprefix",
               "removesuffix", "translate", "get", "pop", "items", "values", "keys", "copy", "read", "readline", "readlines", "json",
               "append", "extend", "add", "update", "setdefault", "group", "groups", "expandtabs", "swapcase", "format_map", "text", "content"}


def dotted(node: ast.AST) -> str | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _is_const(node: ast.AST | None, value) -> bool:
    return isinstance(node, ast.Constant) and node.value == value


# ----------------------------------------------------------------------------------------- one function


class Env(dict):
    """variable name -> tuple of (definition line, taints) that may reach here."""

    def copy(self) -> "Env":
        return Env(self)


def join_envs(*envs: Env) -> Env:
    out = Env()
    for env in envs:
        for name, defs in env.items():
            have = list(out.get(name, ()))
            for d in defs:
                if d not in have:
                    have.append(d)
            out[name] = tuple(have[-MAX_DEFS:])
    return out


class FunctionAnalysis:
    def __init__(self, world: "World", sid: str, file: str, name: str, kind: str, params: list[str], lines: list[str],
                 body: list[ast.stmt], def_line: int, route: bool, record: bool = False):
        self.world, self.sid, self.file, self.name, self.kind = world, sid, file, name, kind
        self.lines, self.body, self.route, self.record = lines, body, route, record
        self.summary = Summary(params=params)
        self.findings: list[Finding] = []
        self.uses: list[tuple[str, int, tuple]] = []
        self.def_line = def_line

    # -- helpers
    def code(self, line: int) -> str:
        return self.lines[line - 1].strip()[:140] if 0 < line <= len(self.lines) else ""

    def initial_env(self) -> Env:
        env = Env()
        for i, p in enumerate(self.summary.params):
            if p in ("self", "cls"):
                env[p] = ((self.def_line, ()),)
                continue
            if self.route:
                t = Taint(f"HTTP handler parameter `{p}`", "http", None, ((self.def_line, self.code(self.def_line), f"parameter `{p}` is filled from the request"),))
            else:
                t = Taint(f"parameter `{p}`", "param", i, ((self.def_line, self.code(self.def_line), f"parameter `{p}`"),))
            env[p] = ((self.def_line, (t,)),)
        return env

    def run(self) -> None:
        env = self.exec_block(self.body, self.initial_env())
        del env

    # -- statements
    def exec_block(self, stmts, env: Env) -> Env:
        for s in stmts:
            env = self.exec_stmt(s, env)
        return env

    def bind(self, target: ast.AST, taints: Taints, env: Env, line: int, weak: bool = False, label: str | None = None) -> None:
        if isinstance(target, ast.Name):
            key = target.id
        elif isinstance(target, (ast.Tuple, ast.List)):
            for el in target.elts:
                self.bind(el.value if isinstance(el, ast.Starred) else el, taints, env, line, weak)
            return
        elif isinstance(target, ast.Attribute):
            key = dotted(target)
            if not key:
                self.ev(target.value, env)
                return
        elif isinstance(target, ast.Subscript):
            base = dotted(target.value)
            self.ev(target.slice, env)
            if not base:
                return
            key, weak = base, True                          # d[k] = v adds to d, never replaces it
        else:
            return
        self.bind_key(key, taints, env, line, weak)

    def bind_key(self, key: str, taints: Taints, env: Env, line: int, weak: bool, note: str | None = None) -> None:
        noted = tuple(t.then(line, self.code(line), note or f"assigned to `{key}`") for t in taints)
        env[key] = tuple([*(env.get(key, ()) if weak else ()), (line, noted)][-MAX_DEFS:])

    def exec_stmt(self, s: ast.stmt, env: Env) -> Env:
        if isinstance(s, ast.Assign):
            taints = self.ev(s.value, env)
            for t in s.targets:
                self.bind(t, taints, env, s.lineno)
        elif isinstance(s, ast.AnnAssign):
            if s.value is not None:
                self.bind(s.target, self.ev(s.value, env), env, s.lineno)
        elif isinstance(s, ast.AugAssign):
            taints = merge(self.ev(s.value, env), self.ev(s.target, env) if not isinstance(s.target, ast.Name) else self.ev_name(s.target, env))
            self.bind(s.target, taints, env, s.lineno, weak=True)
        elif isinstance(s, ast.Expr):
            self.ev(s.value, env)
        elif isinstance(s, ast.Return):
            for t in self.ev(s.value, env):
                if t.param is not None:
                    self.summary.ret_params.add(t.param)
                else:
                    self.summary.ret_concrete = merge(self.summary.ret_concrete, (t,))
        elif isinstance(s, ast.If):
            self.ev(s.test, env)
            a = self.exec_block(s.body, env.copy())
            b = self.exec_block(s.orelse, env.copy())
            env = join_envs(a, b)
        elif isinstance(s, (ast.For, ast.AsyncFor)):
            it = self.ev(s.iter, env)
            entry = env
            for _ in range(2):
                inner = entry.copy()
                self.bind(s.target, it, inner, s.lineno)
                entry = join_envs(entry, self.exec_block(s.body, inner))
            env = join_envs(entry, self.exec_block(s.orelse, entry.copy()))
        elif isinstance(s, ast.While):
            entry = env
            for _ in range(2):
                self.ev(s.test, entry)
                entry = join_envs(entry, self.exec_block(s.body, entry.copy()))
            env = join_envs(entry, self.exec_block(s.orelse, entry.copy()))
        elif isinstance(s, ast.Try) or s.__class__.__name__ == "TryStar":
            after_body = self.exec_block(s.body, env.copy())
            outs = [self.exec_block(s.orelse, after_body.copy())]
            for h in s.handlers:
                henv = join_envs(env, after_body)
                if h.name:
                    henv[h.name] = ((h.lineno, ()),)
                outs.append(self.exec_block(h.body, henv))
            env = join_envs(*outs)
            env = self.exec_block(s.finalbody, env)
        elif isinstance(s, (ast.With, ast.AsyncWith)):
            for item in s.items:
                taints = self.ev(item.context_expr, env)
                if item.optional_vars is not None:
                    self.bind(item.optional_vars, taints, env, s.lineno)
            env = self.exec_block(s.body, env)
        elif isinstance(s, ast.Match):
            self.ev(s.subject, env)
            env = join_envs(*[self.exec_block(c.body, env.copy()) for c in s.cases]) if s.cases else env
        elif isinstance(s, (ast.Raise, ast.Assert)):
            for child in ast.iter_child_nodes(s):
                if isinstance(child, ast.expr):
                    self.ev(child, env)
        elif isinstance(s, ast.Delete):
            for t in s.targets:
                if isinstance(t, ast.Name):
                    env.pop(t.id, None)
        return env

    # -- expressions: return the taints the value may carry, and report sinks met on the way
    def ev(self, n, env: Env) -> Taints:
        if n is None:
            return ()
        handler = getattr(self, "e_" + type(n).__name__, None)
        if handler:
            return handler(n, env)
        out: Taints = ()
        for child in ast.iter_child_nodes(n):
            if isinstance(child, ast.expr):
                out = merge(out, self.ev(child, env))
        return out

    def ev_name(self, n: ast.Name, env: Env) -> Taints:
        defs = env.get(n.id, ())
        if self.record:
            self.uses.append((n.id, n.lineno, tuple(d[0] for d in defs)))
        return merge(*[d[1] for d in defs])

    e_Name = ev_name

    def e_Constant(self, n, env):
        return ()

    def e_Lambda(self, n, env):
        return ()

    def e_Compare(self, n, env):
        for child in ast.iter_child_nodes(n):
            if isinstance(child, ast.expr):
                self.ev(child, env)
        return ()

    def e_Attribute(self, n: ast.Attribute, env: Env) -> Taints:
        chain = dotted(n)
        if chain and chain in env:
            return merge(*[d[1] for d in env[chain]])
        src = self.source_of(chain, n.lineno)
        if src:
            return src
        return self.ev(n.value, env)

    def e_Subscript(self, n: ast.Subscript, env: Env) -> Taints:
        chain = dotted(n.value)
        self.ev(n.slice, env)
        src = self.source_of(chain, n.lineno)
        if src:
            return src
        if chain and chain in env:
            return merge(*[d[1] for d in env[chain]])
        return self.ev(n.value, env)

    def e_JoinedStr(self, n, env):
        return merge(*[self.ev(v.value, env) for v in n.values if isinstance(v, ast.FormattedValue)])

    def e_UnaryOp(self, n, env):
        t = self.ev(n.operand, env)
        return () if isinstance(n.op, ast.Not) else t

    def e_IfExp(self, n, env):
        self.ev(n.test, env)
        return merge(self.ev(n.body, env), self.ev(n.orelse, env))

    def _comp(self, n, env, *elts):
        inner = env.copy()
        for g in n.generators:
            self.bind(g.target, self.ev(g.iter, inner), inner, n.lineno)
            for cond in g.ifs:
                self.ev(cond, inner)
        return merge(*[self.ev(e, inner) for e in elts])

    def e_ListComp(self, n, env):
        return self._comp(n, env, n.elt)

    e_SetComp = e_GeneratorExp = e_ListComp

    def e_DictComp(self, n, env):
        return self._comp(n, env, n.key, n.value)

    def e_NamedExpr(self, n, env):
        t = self.ev(n.value, env)
        self.bind(n.target, t, env, n.lineno)
        return t

    def source_of(self, chain: str | None, line: int) -> Taints:
        if not chain:
            return ()
        parts = chain.split(".")
        label = None
        if chain == "sys.argv":
            label = ("cli", "command-line argument")
        elif chain == "os.environ" or chain.startswith("os.environ."):
            label = ("env", "environment variable")
        elif parts[0] in REQUEST_HEADS or chain.startswith("flask.request"):
            rest = parts[1:] if parts[0] != "flask" else parts[2:]
            if rest and rest[0] in REQUEST_ATTRS:
                label = ("http", f"HTTP request data (`{chain}`)")
        if not label:
            return ()
        return (Taint(label[1], label[0], None, ((line, self.code(line), f"source: {label[1]}"),)),)

    # -- calls
    def e_Call(self, n: ast.Call, env: Env) -> Taints:
        name = dotted(n.func) or ""
        last = name.rsplit(".", 1)[-1] if name else (n.func.attr if isinstance(n.func, ast.Attribute) else "")
        line = n.lineno
        code = self.code(line)
        recv = self.ev(n.func.value, env) if isinstance(n.func, ast.Attribute) else ()
        args = [self.ev(a.value if isinstance(a, ast.Starred) else a, env) for a in n.args]
        kwargs = {(k.arg or f"**{i}"): self.ev(k.value, env) for i, k in enumerate(n.keywords)}
        every = merge(recv, *args, *kwargs.values())

        self.check_sinks(n, name, last, args, kwargs, env)
        if isinstance(n.func, ast.Attribute) and last in MUTATORS:               # parts.append(tainted) taints `parts`
            holder = dotted(n.func.value)
            fed = merge(*args)
            if holder and fed:
                self.bind_key(holder, fed, env, line, weak=True, note=f"added to `{holder}` by .{last}()")

        # sources
        source = SOURCE_CALLS.get(name) or (SOURCE_METHODS.get(last) if isinstance(n.func, ast.Attribute) else None)
        if source:
            cat, label = source
            return merge((Taint(label, cat, None, ((line, code, f"source: {label}"),)),), recv if last in STR_METHODS else ())

        # sanitisers
        if name in CLEAN_ALL:
            return ()
        kinds = CLEAN_KINDS.get(name)
        if kinds:
            return tuple(t.clean(kinds).then(line, code, f"sanitised by {name}() for {', '.join(sorted(kinds))}") for t in every)

        # user-defined callee resolved by the call graph: apply its summary
        callee = self.world.callee_at(self.sid, n, last)
        if callee is not None:
            return self.apply_summary(callee, n, name or last, args, kwargs, recv)

        # anything else: the value is assumed to pass through. A method on a tainted object (`conn.execute(...)`) does not
        # hand back the object's taint unless it is a data method like .strip() or .get(); its arguments still count.
        if isinstance(n.func, ast.Attribute) and last not in STR_METHODS:
            every = merge(*args, *kwargs.values())
        if name in URL_ONLY and args:                      # Request(url, data=...): only the URL decides where it goes
            every = args[0]
        if not every or name in ("print", "len", "isinstance"):
            return ()
        sure = name in KEEPS or (isinstance(n.func, ast.Attribute) and last in STR_METHODS)
        note = f"passed through {name or last}()" + ("" if sure else " (assumed to keep the value)")
        return tuple(t.then(line, code, note, assumed=not sure) for t in every)

    def apply_summary(self, callee: "CalleeRef", n: ast.Call, shown: str, args: list[Taints], kwargs: dict[str, Taints], recv: Taints) -> Taints:
        summary = callee.summary
        offset = 1 if summary.params[:1] in (["self"], ["cls"]) and (isinstance(n.func, ast.Attribute) or callee.kind == "method") else 0
        by_index: dict[int, Taints] = {}
        for i, a in enumerate(args):
            by_index[i + offset] = a
        for k, v in kwargs.items():
            if k in summary.params:
                by_index[summary.params.index(k)] = v
        line, code = n.lineno, self.code(n.lineno)
        conf_note = "" if callee.conf == "high" else f" (call resolved with {callee.conf} confidence)"
        for idx, hits in summary.sink_params.items():
            for t in by_index.get(idx, ()):
                pname = summary.params[idx] if idx < len(summary.params) else str(idx)
                bridge = (line, code, f"passed to {callee.name}() as `{pname}`{conf_note}")
                for hit in hits:
                    if hit.kind in t.cleaned:
                        continue
                    steps = cap((*t.steps, bridge, *hit.steps[1:]))       # hit.steps[0] is the parameter's own entry
                    if t.param is None:
                        self.report(hit.kind, hit.file, hit.sid, hit.line, hit.sink, t, steps, callee.conf)
                    else:
                        self.add_hit(t.param, Hit(hit.kind, hit.line, hit.file, hit.sid, steps, hit.sink))
        out: list[Taint] = []
        for idx in summary.ret_params:
            for t in by_index.get(idx, ()):
                out.append(t.then(line, code, f"returned by {callee.name}(){conf_note}"))
        for t in summary.ret_concrete:
            out.append(t.then(line, code, f"returned by {callee.name}()"))
        return merge(tuple(out))

    # -- sinks
    def match_sink(self, n: ast.Call, name: str, last: str) -> tuple[str, list[int]] | None:
        if name in COMMAND:
            return "command-exec", [0]
        if name.startswith("os.") and last.startswith(("exec", "spawn")) and last not in ("exec_",):
            return "command-exec", [0, 1]
        if name in SUBPROCESS:
            shell = any(k.arg == "shell" and not _is_const(k.value, False) for k in n.keywords)
            first = n.args[0] if n.args else None
            if shell or (first is not None and not isinstance(first, (ast.List, ast.Tuple))):
                return "command-exec", [0]
            return None
        if name in CODE:
            return "code-exec", [0]
        if isinstance(n.func, ast.Attribute) and last in SQL_METHODS:
            return "sql", [0]
        if name in PATH_ONE:
            return "path", [0]
        if name in PATH_TWO:
            return "path", [0, 1]
        if name in DESER:
            return "deserialization", [0]
        if name in ("yaml.load", "yaml.load_all"):
            loader = next((k.value for k in n.keywords if k.arg == "Loader"), None)
            if loader is None or "Safe" not in (dotted(loader) or ""):
                return "deserialization", [0]
            return None
        if name in SSRF:
            return "ssrf", [0]
        if name in IMPORT:
            return "import", [0]
        if name in XSS:
            return "xss", [0]
        return None

    def check_sinks(self, n: ast.Call, name: str, last: str, args: list[Taints], kwargs: dict[str, Taints], env: Env) -> None:
        matched = self.match_sink(n, name, last)
        if not matched:
            return
        kind, idxs = matched
        chosen = [args[i] for i in idxs if i < len(args)]
        if not chosen:
            chosen = [v for k, v in kwargs.items() if k in SINK_KWARGS]
        sink = f"{name or last}()"
        step = (n.lineno, self.code(n.lineno), f"sink: {SINK_INFO[kind][0].lower()} via {sink}")
        for t in merge(*chosen):
            if kind in t.cleaned:
                continue
            if t.param is not None:
                self.add_hit(t.param, Hit(kind, n.lineno, self.file, self.sid, cap((*t.steps, step)), sink))
            else:
                self.report(kind, self.file, self.sid, n.lineno, sink, t, cap((*t.steps, step)), "high")

    def add_hit(self, idx: int, hit: Hit) -> None:
        hits = self.summary.sink_params.setdefault(idx, [])
        if not any((h.kind, h.file, h.line) == (hit.kind, hit.file, hit.line) for h in hits):
            hits.append(hit)

    def report(self, kind: str, sink_file: str, sink_sid: str, line: int, sink: str, t: Taint, steps: tuple, edge_conf: str) -> None:
        if kind in ("path", "ssrf", "import") and t.category in ("cli", "env", "input"):
            return                                # a script reading its own arguments is expected, not a vulnerability
        title, cwe, severity, fix = SINK_INFO[kind]
        conf = "low" if edge_conf == "low" else "med" if (t.assumed or edge_conf == "med") else "high"
        self.world.add_finding(Finding(kind, title, cwe, severity, sink_file, line, sink_sid, self.sid, sink, t.origin, t.category, steps, conf, fix))


# ----------------------------------------------------------------------------------------- the whole project


@dataclass
class CalleeRef:
    name: str
    kind: str
    conf: str
    summary: Summary


class World:
    def __init__(self, db, read_source):
        self.db = db
        self.read = read_source
        self.symbols = {r["id"]: r for r in db.execute("SELECT id, file, kind, name, qname, start FROM symbols")}
        self.calls: dict[tuple[str, int], list[tuple[str, str]]] = {}
        for r in db.execute("SELECT src, dst, line, conf FROM edges WHERE type='CALLS' AND dst NOT LIKE '?%' AND dst NOT LIKE 'ext:%' AND conf IN ('high','med')"):
            self.calls.setdefault((r["src"], r["line"]), []).append((r["dst"], r["conf"]))
        self.summaries: dict[str, Summary] = {}
        self.findings: dict[tuple, Finding] = {}
        self.functions: list[dict] = []
        self.skipped: list[tuple[str, str]] = []
        self.current: tuple[str, str] = ("", "")

    def callee_at(self, src: str, node: ast.Call, last: str) -> CalleeRef | None:
        line = getattr(node.func, "end_lineno", None) or node.lineno
        at = self.calls.get((src, line), [])
        options = [(d, c) for d, c in at if d in self.summaries and self.symbols[d]["name"] == last]
        if not options and isinstance(node.func, ast.Name):                    # `Foo(x)` runs Foo.__init__
            options = [(f"{d}.__init__", c) for d, c in at if self.symbols[d]["kind"] == "class" and self.symbols[d]["name"] == last
                       and f"{d}.__init__" in self.summaries]
        if len(options) != 1:
            return None
        dst, conf = options[0]
        return CalleeRef(self.symbols[dst]["name"], self.symbols[dst]["kind"], conf, self.summaries[dst])

    def add_finding(self, f: Finding) -> None:
        key = (f.kind, f.file, f.line, f.category)             # one report per sink and kind of source: keep the clearest path
        old = self.findings.get(key)
        if old is None or (CONF_ORDER[f.confidence], len(f.steps)) < (CONF_ORDER[old.confidence], len(old.steps)):
            self.findings[key] = f

    # -- driving the analysis
    def collect(self, files: list[str]) -> None:
        for path in files:
            text = self.read(path)
            if text is None:
                self.skipped.append((path, "could not be read"))
                continue
            try:
                tree = ast.parse(text, filename=path)
            except (SyntaxError, ValueError, RecursionError) as exc:
                self.skipped.append((path, f"does not parse ({type(exc).__name__})"))
                continue
            lines = text.splitlines()
            seen: dict[str, int] = {}
            module_id = f"{path}::<module>"
            module_body = [s for s in tree.body if not isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
            self.functions.append({"sid": module_id, "file": path, "name": "<module>", "kind": "module", "params": [], "lines": lines,
                                   "body": module_body, "line": 1, "route": False})
            self._walk(tree.body, "", "module", path, lines, seen)

    def _walk(self, nodes, parent_q: str, parent_kind: str, path: str, lines: list[str], seen: dict[str, int]) -> None:
        for node in nodes:
            for child in _defs_in(node):
                q = child.name if not parent_q else f"{parent_q}.{child.name}"
                base = f"{path}::{q}"
                seen[base] = seen.get(base, 0) + 1
                unique = q if seen[base] == 1 else f"{q}#{seen[base]}"
                if isinstance(child, ast.ClassDef):
                    self._walk(child.body, q, "class", path, lines, seen)
                    continue
                a = child.args
                params = [x.arg for x in [*a.posonlyargs, *a.args, *a.kwonlyargs]]
                route = any(_is_route_decorator(dotted(d.func if isinstance(d, ast.Call) else d) or "") for d in child.decorator_list)
                self.functions.append({"sid": f"{path}::{unique}", "file": path, "name": child.name, "kind": "method" if parent_kind == "class" else "function",
                                       "params": params, "lines": lines, "body": child.body, "line": child.lineno, "route": route})
                self._walk(child.body, q, "function", path, lines, seen)

    def run(self) -> None:
        for fn in self.functions:
            self.summaries[fn["sid"]] = Summary(params=fn["params"])
        last: dict[str, tuple] = {}
        for _ in range(ROUNDS):
            self.findings = {}
            changed = False
            for fn in self.functions:
                fa = FunctionAnalysis(self, fn["sid"], fn["file"], fn["name"], fn["kind"], fn["params"], fn["lines"], fn["body"], fn["line"], fn["route"])
                fa.run()
                sig = fa.summary.signature()
                if last.get(fn["sid"]) != sig:
                    changed = True
                last[fn["sid"]] = sig
                self.summaries[fn["sid"]] = fa.summary
            if not changed:
                break

    def analyse_one(self, sid: str) -> FunctionAnalysis | None:
        fn = next((f for f in self.functions if f["sid"] == sid), None)
        if fn is None:
            return None
        fa = FunctionAnalysis(self, fn["sid"], fn["file"], fn["name"], fn["kind"], fn["params"], fn["lines"], fn["body"], fn["line"], fn["route"], record=True)
        fa.run()
        return fa


def _is_route_decorator(name: str) -> bool:
    """`app.route`, `router.get`, `bp.post`, ... and a bare `@route(...)` (bottle-style). A bare `get` or `post` is too common a name to trust."""
    last = name.rsplit(".", 1)[-1]
    return last in ROUTE_DECORATORS and ("." in name or last == "route")


def _defs_in(node: ast.AST):
    """Function and class definitions directly inside `node` in source order, not descending into them."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        yield node
        return
    for child in ast.iter_child_nodes(node):
        yield from _defs_in(child)


SEVERITY_ORDER = {"HIGH": 0, "MEDIUM": 1}
CONF_ORDER = {"high": 0, "med": 1, "low": 2}


def analyse(atlas, include_tests: bool = False) -> dict:
    """Run the analysis over every parsable Python file in the graph."""
    from .queries import is_test_id

    rows = atlas.db.execute("SELECT path, error FROM files WHERE lang='python' ORDER BY path").fetchall()
    files = [r["path"] for r in rows if include_tests or not is_test_id(r["path"])]

    def read(path: str) -> str | None:
        try:
            return (atlas.root / path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

    world = World(atlas.db, read)
    world.collect(files)
    world.run()
    findings = sorted(world.findings.values(), key=lambda f: (SEVERITY_ORDER[f.severity], CONF_ORDER[f.confidence], f.file, f.line))
    return {"findings": findings, "files": len(files) - len(world.skipped), "functions": len(world.functions),
            "skipped": world.skipped, "world": world}


def reaching_definitions(atlas, sid: str, variable: str) -> dict:
    """Which assignments can reach each use of `variable` inside one function, and whether any carries tainted data."""
    result = analyse(atlas, include_tests=True)
    fa = result["world"].analyse_one(sid)
    if fa is None:
        return {"error": "not a Python function or module"}
    lines = fa.lines
    uses = []
    for name, line, defs in fa.uses:
        if name == variable or name.startswith(variable + "."):
            uses.append({"line": line, "code": fa.code(line), "defs": [{"line": d, "code": fa.code(d)} for d in sorted(set(defs))]})
    params = fa.summary.params
    unique, seen = [], set()
    for u in uses:
        key = (u["line"], tuple(d["line"] for d in u["defs"]))
        if key not in seen:
            seen.add(key)
            unique.append(u)
    return {"function": sid, "variable": variable, "uses": unique, "is_parameter": variable in params, "line_count": len(lines)}
