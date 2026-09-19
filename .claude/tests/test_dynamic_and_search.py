from atlas_engine import dynamic, reports
from atlas_engine.indexer import index
from atlas_engine.queries import Atlas

from conftest import write

FILES = {
    "app/registry.py": '''
        HANDLERS = {}

        def register(name):
            def deco(fn):
                HANDLERS[name] = fn
                return fn
            return deco
    ''',
    "app/handlers.py": '''
        from .registry import register

        @register("greet")
        def greet():
            return "hi"

        def add():
            return 1

        def sub():
            return 2

        def truly_unused():
            return 3

        class Base:
            def hook(self):
                return 1

        class Kid(Base):
            def hook(self):
                return 2

        class Ctx:
            def __enter__(self):
                return self
    ''',
    "app/dispatch.py": '''
        from . import handlers

        OPS = {"sub": "sub"}

        def run(op):
            return getattr(handlers, op)()

        def literal():
            return getattr(handlers, "add")

        def use_base(b: handlers.Base):
            return b.hook()
    ''',
}


def atlas_for(tmp_path, files):
    (tmp_path / ".claude" / "atlas").mkdir(parents=True)
    write(tmp_path, files)
    index(tmp_path, full=True)
    return Atlas(tmp_path)


def by_name(result):
    return {section: {x["symbol"]["qname"]: x for x in result[section]} for section in ("unreferenced", "possible", "implicit")}


def test_unused_separates_dead_code_from_hidden_use(tmp_path):
    atlas = atlas_for(tmp_path, FILES)
    found = by_name(atlas.unused())
    assert "truly_unused" in found["unreferenced"]
    assert found["implicit"]["greet"]["evidence"][0]["kind"] == "decorator"        # handed to a registry by @register
    assert "getattr" in {e["kind"] for e in found["implicit"]["add"]["evidence"]}   # getattr(handlers, "add")
    assert "override" in {e["kind"] for e in found["implicit"]["Kid.hook"]["evidence"]}
    assert "special-method" in {e["kind"] for e in found["implicit"]["Ctx.__enter__"]["evidence"]}
    assert "sub" in found["possible"]                                                # only a string "sub" in a dict
    assert "greet" not in found["unreferenced"] and "add" not in found["unreferenced"]


def test_dynamic_lookup_in_a_file_is_a_lead_for_its_neighbours(tmp_path):
    atlas = atlas_for(tmp_path, {"m.py": "def run(op):\n    return globals()[op]()\n\ndef target():\n    return 1\n"})
    d = dynamic.dynamic_use(atlas, atlas.db.execute("SELECT * FROM symbols WHERE name='target'").fetchone())
    assert d["level"] == "possible" and d["evidence"][0]["kind"] == "lookup"


def test_context_and_impact_show_hidden_use_and_plain_code_stays_quiet(tmp_path):
    atlas = atlas_for(tmp_path, FILES)
    md = reports.md_context(atlas.context("app/handlers.py::greet"))
    assert "Possible use the graph cannot see" in md and "@register" in md
    impact = reports.md_impact(atlas.impact("greet"))
    assert "@register" in impact and "Grep for dynamic use" in impact
    quiet = reports.md_context(atlas.context("app/handlers.py::truly_unused"))
    assert "Possible use the graph cannot see" not in quiet and "no sign of dynamic use" in quiet


def test_benign_decorators_are_not_dynamic(tmp_path):
    atlas = atlas_for(tmp_path, {"m.py": "import functools\n\nclass A:\n    @property\n    def x(self):\n        return 1\n\n    @staticmethod\n    def s():\n        return 1\n\n@functools.lru_cache\ndef f():\n    return 1\n"})
    assert atlas.db.execute("SELECT COUNT(*) FROM dynamic WHERE kind='decorator'").fetchone()[0] == 0


def test_docstrings_and_fstrings_are_not_name_strings(tmp_path):
    atlas = atlas_for(tmp_path, {"m.py": 'def f():\n    """secret_name"""\n    return f"prefix_{1}"\n'})
    assert atlas.db.execute("SELECT COUNT(*) FROM strings WHERE value IN ('secret_name','prefix_')").fetchone()[0] == 0


def test_dynamic_tables_follow_file_changes(tmp_path):
    atlas = atlas_for(tmp_path, FILES)
    assert atlas.db.execute("SELECT COUNT(*) FROM dynamic WHERE file='app/dispatch.py'").fetchone()[0] >= 2
    (tmp_path / "app/dispatch.py").unlink()
    index(tmp_path)
    assert Atlas(tmp_path).db.execute("SELECT COUNT(*) FROM dynamic WHERE file='app/dispatch.py'").fetchone()[0] == 0


SEARCH_FILES = {
    "auth/login.py": '''
        from .crypto import hash_password

        def login(user, password):
            """Authenticate a user against the stored credentials."""
            return hash_password(password) == user.stored
    ''',
    "auth/crypto.py": '''
        def hash_password(pw):
            return pw[::-1]
    ''',
    "auth/session.py": '''
        from .login import login

        def open_session(user, password):
            """Start a session."""
            return login(user, password)
    ''',
    "billing/invoice.py": '''
        def make_invoice(items):
            """Create an invoice for the items."""
            return sum(items)
    ''',
}


def test_graph_search_reaches_code_the_words_do_not_mention(tmp_path):
    atlas = atlas_for(tmp_path, SEARCH_FILES)
    out = atlas.graph_search("user authentication")
    got = {r["qname"]: r for r in out["results"]}
    assert got["login"]["direct"] and got["login"]["why"] == "matches the description"
    assert not got["hash_password"]["direct"] and got["hash_password"]["why"] == "is called by login"   # never says "authentication"
    assert "open_session" in got
    assert "make_invoice" not in got
    assert list(got)[0] == "login"


def test_graph_search_reports_and_edge_cases(tmp_path):
    atlas = atlas_for(tmp_path, SEARCH_FILES)
    md = reports.md_graph_search(atlas.graph_search("authentication"))
    assert "why it is here" in md and "is called by login" in md
    assert "No symbols matched" in reports.md_graph_search(atlas.graph_search("zzzqqq"))
    assert atlas.graph_search("the of and")["results"] == []          # only stop words
    assert reports.md_search("x", [])                                 # keyword search unchanged


def kinds(atlas, name):
    sid = atlas.db.execute("SELECT id FROM symbols WHERE qname=?", (name,)).fetchone()["id"]
    return {e["kind"] for e in dynamic.dynamic_use(atlas, atlas.symbol(sid))["evidence"]}


def test_naming_conventions_properties_and_external_bases(tmp_path):
    atlas = atlas_for(tmp_path, {"m.py": '''
        import ast
        from http.server import BaseHTTPRequestHandler

        class Walker(ast.NodeVisitor):
            def visit_Name(self, node):
                return 1

            def _seq(self, node):
                return 2

            visit_List = _seq

        class Router:
            def on_open(self):
                return 1

            def dispatch(self, kind):
                return getattr(self, "on_" + kind)()

            @property
            def size(self):
                return 3

        class Web(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                return None
    '''})
    assert "convention" in kinds(atlas, "Walker.visit_Name")
    assert "prefix-dispatch" in kinds(atlas, "Router.on_open")
    assert "property" in kinds(atlas, "Router.size")
    assert "external-base" in kinds(atlas, "Web.log_message")
    # `visit_List = _seq` in a class body is a reference to the sibling method, so _seq is not orphaned
    unreferenced = {x["symbol"]["qname"] for x in atlas.unused()["unreferenced"]}
    assert "Walker._seq" not in unreferenced


def test_underscore_class_receivers_resolve(tmp_path):
    atlas = atlas_for(tmp_path, {"m.py": "class _Worker:\n    def run(self):\n        return 1\n\ndef go():\n    return _Worker().run()\n"})
    edges = {(r["src"].split("::")[-1], r["dst"].split("::")[-1]) for r in atlas.db.execute("SELECT src, dst FROM edges WHERE type='CALLS'")}
    assert ("go", "_Worker.run") in edges


def test_calls_on_untyped_variables_are_leads_not_proof_of_dead_code(tmp_path):
    atlas = atlas_for(tmp_path, {"shop.py": '''
        class Order:
            def total(self):
                return 1

            def cancel(self):
                return 2

        def make():
            return Order()

        def report(orders):
            first = orders[0]
            return first.total()
    '''})
    assert "untyped-call" in kinds(atlas, "Order.total")            # `first.total()`: the receiver's type is unknown
    assert "untyped-call" not in kinds(atlas, "Order.cancel")       # nothing calls .cancel() anywhere
    found = {x["symbol"]["qname"]: x for x in atlas.unused()["possible"]}
    assert "Order.total" in found and "Order.cancel" not in found
