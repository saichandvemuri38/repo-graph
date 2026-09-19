"""Evidence that a symbol is used in ways the call graph cannot see.

A static graph misses calls that go through a name looked up at run time (`getattr(obj, name)()`, `globals()[name]`,
`importlib.import_module`), functions a decorator hands to a framework (`@app.route`, `@pytest.fixture`), methods
called through a base class, and special methods Python calls itself. This module collects what the parsers recorded
(tables `dynamic` and `strings`) plus a few graph facts, and reports it next to a symbol so "nothing calls this" is
never claimed on the edges alone.

Strong evidence means the symbol is very likely reached without a visible call. Weak evidence is a lead to check.
"""

from __future__ import annotations

MAX_EVIDENCE = 8
# Base classes whose subclasses are called by name convention: base -> method-name prefixes (or exact names ending in "!").
CONVENTIONS = {
    "NodeVisitor": ("visit_", "generic_visit!"), "NodeTransformer": ("visit_", "generic_visit!"),
    "TestCase": ("test", "setUp!", "tearDown!", "setUpClass!", "tearDownClass!", "setUpModule!"),
    "HTMLParser": ("handle_",), "Cmd": ("do_", "help_", "complete_"), "BaseHTTPRequestHandler": ("do_",),
    "Thread": ("run!",), "Process": ("run!",), "JSONEncoder": ("default!",), "Formatter": ("format!",),
    "Handler": ("emit!",), "Command": ("handle!", "add_arguments!"), "BaseCommand": ("handle!", "add_arguments!"),
    "View": ("get!", "post!", "put!", "delete!", "dispatch!"), "APIView": ("get!", "post!", "put!", "delete!", "patch!"),
    "Model": ("save!", "clean!"), "ABC": (), "Protocol": (),
}
FILE_LOOKUP_KINDS = ("getattr-dynamic", "namespace-lookup", "dynamic-import", "eval")


def _sym(row) -> dict:
    return dict(row) if not isinstance(row, dict) else row


def _ancestors(db, class_id: str, limit: int = 6) -> list[str]:
    """Class ids reached by following EXTENDS edges upward."""
    seen, frontier, out = {class_id}, [class_id], []
    for _ in range(limit):
        nxt = []
        for cid in frontier:
            for r in db.execute("SELECT dst FROM edges WHERE type='EXTENDS' AND src=? AND dst NOT LIKE '?%' AND dst NOT LIKE 'ext:%'", (cid,)):
                if r["dst"] not in seen:
                    seen.add(r["dst"]); nxt.append(r["dst"]); out.append(r["dst"])
        frontier = nxt
        if not frontier:
            break
    return out


class Evidence:
    """The recorded dynamic sites, loaded once so judging thousands of symbols does not query thousands of times."""

    def __init__(self, db):
        self.db = db
        self.strings: dict[str, list] = {}
        for r in db.execute("SELECT value, file, line FROM strings ORDER BY file, line"):
            self.strings.setdefault(r["value"], []).append((r["file"], r["line"]))
        self.tails: dict[str, list] = {}           # "pkg.mod.name" -> by last part, so `name` finds dotted strings too
        for value, where in self.strings.items():
            if "." in value:
                self.tails.setdefault(value.rsplit(".", 1)[-1], []).extend(where)
        self.literal: dict[str, list] = {}
        self.prefix: dict[str, list] = {}
        self.lookups: dict[str, list] = {}
        self.by_src: dict[tuple, list] = {}
        for r in db.execute("SELECT file, src, kind, detail, line FROM dynamic ORDER BY line"):
            if r["kind"] == "getattr-literal":
                self.literal.setdefault(r["detail"], []).append((r["file"], r["line"]))
            elif r["kind"] == "getattr-prefix":
                self.prefix.setdefault(r["file"], []).append((r["detail"], r["line"]))
            elif r["kind"] in FILE_LOOKUP_KINDS:
                self.lookups.setdefault(r["file"], []).append((r["detail"], r["line"]))
            if r["kind"] in ("decorator", "property"):
                self.by_src.setdefault((r["src"], r["kind"]), []).append((r["detail"], r["line"]))
        self.unresolved: dict[str, list] = {}
        for r in db.execute("SELECT file, line, name FROM unresolved ORDER BY file, line"):
            self.unresolved.setdefault(r["name"].rsplit(".", 1)[-1], []).append((r["file"], r["line"], r["name"]))
        self.bases: dict[str, set] = {}
        for r in db.execute("SELECT src, target FROM raw_edges WHERE type='EXTENDS'"):
            self.bases.setdefault(r["src"], set()).add(r["target"].rsplit(".", 1)[-1])
        self.entries = {r["entry"]: r["name"] for r in db.execute("SELECT entry, name FROM processes")}
        self.lang = {r["path"]: r["lang"] for r in db.execute("SELECT path, lang FROM files")}


def dynamic_use(atlas, sym, evidence: "Evidence | None" = None) -> dict:
    """{"level": likely | possible | none | unassessed, "evidence": [{"strength", "kind", "text"}]} for one symbol row.

    `unassessed` means the language has no dynamic-use analysis, so "none" would be a claim we cannot back."""
    from .queries import is_test_id          # imported here: queries imports this module

    db = atlas.db
    ev_index = evidence or Evidence(db)
    s = _sym(sym)
    sid, name, qname, file, kind = s["id"], s["name"], s["qname"], s["file"], s["kind"]
    ev: list[dict] = []

    def add(strength: str, k: str, text: str) -> None:
        if len(ev) < 4 * MAX_EVIDENCE:
            ev.append({"strength": strength, "kind": k, "text": text})

    if kind == "module":
        return {"level": "none", "evidence": []}
    if is_test_id(file) and name.startswith(("test", "Test")):
        add("strong", "test", "a test, collected and run by the test runner")
    if kind == "method" and name.startswith("__") and name.endswith("__"):
        add("strong", "special-method", "a special method: Python calls it implicitly (construction, `with`, `len()`, `==` ...)")
    for detail, line in ev_index.by_src.get((sid, "property"), []):
        add("strong", "property", f"a property (`@{detail}`): it is read as an attribute, and attribute reads are not calls in the graph")
    if sid in ev_index.entries:
        add("strong", "entry", f"the entry point of flow “{ev_index.entries[sid]}”: it is called from outside the code, so no caller is expected")
    for detail, line in ev_index.by_src.get((sid, "decorator"), []):
        add("strong", "decorator", f"decorated with `@{detail}` (line {line}): the framework or registry that owns it may call it")
    if kind in ("method", "function"):
        for prefix, line in ev_index.prefix.get(file, []):
            if prefix and name.startswith(prefix) and name != prefix:
                add("strong", "prefix-dispatch", f"the name starts with '{prefix}', and `getattr` builds names from that prefix at line {line}")
                break
    outside: list[str] = []
    if kind == "method" and "." in qname:
        owner = f"{file}::{qname.rsplit('.', 1)[0]}"
        for anc in _ancestors(db, owner):
            base = db.execute("SELECT id, qname FROM symbols WHERE id=?", (anc,)).fetchone()
            if base and db.execute("SELECT 1 FROM symbols WHERE id=?", (f"{base['id']}.{name}",)).fetchone():
                add("strong", "override", f"overrides `{base['qname']}.{name}`: calls made through the base class reach this method")
                break
        base_names = ev_index.bases.get(owner, set())
        known = set()
        for r in db.execute("SELECT s.name FROM edges e JOIN symbols s ON s.id=e.dst WHERE e.type='EXTENDS' AND e.src=?", (owner,)):
            known.add(r["name"])
        outside = sorted(base_names - known)
        for base in base_names & CONVENTIONS.keys():
            hit = [p for p in CONVENTIONS[base] if p.endswith("!") and name == p[:-1]] or [p for p in CONVENTIONS[base] if not p.endswith("!") and name.startswith(p)]
            if hit:
                add("strong", "convention", f"a `{base}` subclass: the framework calls `{name}` by its naming convention")
                break
    if outside and not name.startswith("__") and kind == "method":
        add("weak", "external-base", f"the class extends `{outside[0]}` from outside this repo; that base class may call `{name}`")
    for f, line in ev_index.literal.get(name, [])[:MAX_EVIDENCE]:
        add("strong", "getattr", f"`getattr`/`hasattr`/`setattr` is called with the literal '{name}' at {f}:{line}")
    if len(name) >= 3:
        for f, line in [*ev_index.strings.get(name, []), *ev_index.tails.get(name, [])][:MAX_EVIDENCE]:
            add("weak", "string", f"the name appears as a string at {f}:{line} (a dispatch table, `__all__`, a config key?)")
    for detail, line in ev_index.lookups.get(file, [])[:3]:
        add("weak", "lookup", f"this file looks names up at run time (`{detail}` at line {line}), which could reach it")
    for f, line, written in ev_index.unresolved.get(name, [])[:3]:
        add("weak", "unresolved", f"a call the graph could not resolve, `{written}` at {f}:{line}, may target it")
    if ev_index.lang.get(file) != "python" and not any(e["strength"] == "strong" for e in ev):
        return {"level": "unassessed" if not ev else "possible", "evidence": ev}
    level = "likely" if any(e["strength"] == "strong" for e in ev) else "possible" if ev else "none"
    return {"level": level, "evidence": ev}


def unused(atlas, limit: int = 60) -> dict:
    """Symbols with no incoming call, reference or inheritance edge, sorted by whether anything hints at hidden use."""
    from .queries import is_test_id

    rows = atlas.db.execute(
        "SELECT s.* FROM symbols s WHERE s.kind IN ('function','method','class') AND NOT EXISTS "
        "(SELECT 1 FROM edges e WHERE e.dst=s.id AND e.type IN ('CALLS','REFERENCES','EXTENDS')) ORDER BY s.file, s.start").fetchall()
    evidence = Evidence(atlas.db)
    buckets: dict[str, list] = {"unreferenced": [], "possible": [], "implicit": [], "unassessed": []}
    total = 0
    for r in rows:
        if is_test_id(r["file"]):
            continue
        total += 1
        d = dynamic_use(atlas, r, evidence)
        key = {"none": "unreferenced", "possible": "possible", "likely": "implicit", "unassessed": "unassessed"}[d["level"]]
        buckets[key].append({"symbol": dict(r), **d})
    return {"total": total, "counts": {k: len(v) for k, v in buckets.items()},
            "unreferenced": buckets["unreferenced"][:limit], "possible": buckets["possible"][:limit], "implicit": buckets["implicit"][:limit],
            "unassessed": buckets["unassessed"][:limit]}
