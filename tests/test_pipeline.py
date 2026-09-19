import subprocess
import time

from atlas_engine.config import Paths
from atlas_engine.indexer import index
from atlas_engine.queries import Atlas

from conftest import write


def edges(root, where="1=1"):
    atlas = Atlas(root)
    rows = atlas.db.execute(f"SELECT type, src, dst, conf FROM edges WHERE {where}").fetchall()
    atlas.store.close()
    return {(r["type"], r["src"].split("::")[-1], r["dst"].split("::")[-1], r["conf"]) for r in rows}


def test_cross_file_resolution(project):
    index(project, full=True, publish=False)
    e = edges(project)
    assert ("CALLS", "Circle.area", "clamp", "high") in e                 # re-export through pkg/__init__.py
    assert ("CALLS", "handler", "clamp", "high") in e                     # `import pkg.util` then pkg.util.clamp
    assert ("CALLS", "handler", "make_circle", "high") in e               # from-import
    assert ("EXTENDS", "Circle", "Shape", "high") in e
    assert ("REFERENCES", "handler", "register", "high") in e             # decorator
    assert ("CALLS", "Shape.describe", "Shape.area", "high") in e         # self.method


def test_inferred_receivers_are_capped_at_med(project):
    index(project, full=True, publish=False)
    e = edges(project)
    assert ("CALLS", "handler", "Circle.area", "med") in e                # c = make_circle() -> Circle via return annotation
    assert ("CALLS", "report", "Shape.describe", "med") in e              # parameter annotation + inherited method


def test_broken_file_is_recovered_not_dropped(project):
    result = index(project, full=True, publish=False)
    assert [p for p, _ in result.parse_errors] == ["day-1.py"]
    assert Atlas(project).find("twoSum")[1] == "exact"


def test_incremental_reparses_only_what_changed(project):
    index(project, full=True, publish=False)
    again = index(project, publish=False)
    assert not again.changed and again.unchanged == again.scanned
    write(project, {"pkg/util.py": "def clamp(x, lo, hi):\n    return x\n\ndef brand_new():\n    return 2\n"})
    result = index(project, publish=False)
    assert result.modified == ["pkg/util.py"] and not result.added
    assert Atlas(project).find("brand_new")[1] == "exact"
    assert Atlas(project).find("unused_helper")[1] != "exact"


def test_cross_file_edges_update_when_a_dependency_moves(project):
    index(project, full=True, publish=False)
    # Removing clamp must not leave dangling ids: callers' edges become unresolved instead.
    write(project, {"pkg/util.py": "def other():\n    return 1\n"})
    index(project, publish=False)
    atlas = Atlas(project)
    assert not any(r["dst"].endswith("::clamp") for r in atlas.db.execute("SELECT dst FROM edges"))
    assert {"clamp"} <= {r["name"].rsplit(".", 1)[-1] for r in atlas.db.execute("SELECT name FROM unresolved")}


def test_removed_files_leave_no_trace(project):
    index(project, full=True, publish=False)
    (project / "day-1.py").unlink()
    result = index(project, publish=False)
    assert result.removed == ["day-1.py"]
    atlas = Atlas(project)
    assert atlas.db.execute("SELECT COUNT(*) FROM symbols WHERE file='day-1.py'").fetchone()[0] == 0
    assert atlas.db.execute("SELECT COUNT(*) FROM symbols_fts WHERE file='day-1.py'").fetchone()[0] == 0
    assert not (Paths(project).shards / "day-1.py.graph.md").exists()


def test_shards_follow_the_documented_format(project):
    index(project, full=True, publish=False)
    text = (Paths(project).shards / "pkg__shapes.py.graph.md").read_text()
    lines = [l for l in text.splitlines() if l and not l.startswith("#")]
    assert lines[0].startswith("FILE|pkg/shapes.py|python|")
    assert any(l.startswith("SYM|pkg/shapes.py::Circle.area|method|") for l in lines)
    assert any(l.startswith("EDGE|CALLS|pkg/shapes.py::Circle.area|pkg/util.py::clamp|") and l.endswith("|high") for l in lines)
    assert all(l.split("|", 1)[0] in ("FILE", "SYM", "EDGE") for l in lines)


def test_html_pages_are_generated_and_escaped(project):
    write(project, {"weird.py": "def f(a: 'x<script>alert(1)</script>'):\n    pass\n"})
    index(project, full=True)
    app = Paths(project).app
    for page in ("index", "symbols", "flows", "clusters", "impact", "debug", "changes"):
        assert (app / f"{page}.html").is_file()
    symbols = (app / "symbols.html").read_text()
    assert "<script>alert(1)</script>" not in symbols
    assert "Circle.area" in symbols


def test_impact_reports_dependants_by_depth_and_risk(project):
    index(project, full=True, publish=False)
    r = Atlas(project).impact("clamp")
    assert {s.split("::")[-1] for s in r["levels"][1]} == {"Circle.area", "handler"}
    assert "main" in {s.split("::")[-1] for s in r["levels"][2]}
    assert r["risk"] in ("LOW", "MEDIUM", "HIGH", "CRITICAL")
    down = Atlas(project).impact("handler", "downstream")
    assert "clamp" in {s.split("::")[-1] for lv in down["levels"].values() for s in lv}


def test_tests_are_reported_separately_and_do_not_raise_risk(project):
    write(project, {"tests/test_util.py": "from pkg.util import clamp\n\ndef test_clamp():\n    assert clamp(5, 0, 3) == 3\n"})
    index(project, full=True, publish=False)
    r = Atlas(project).impact("clamp")
    assert {k.split("::")[-1] for k in r["tests"]} == {"test_clamp"}
    assert "test_clamp" not in {s.split("::")[-1] for lv in r["levels"].values() for s in lv}
    assert r["affected"] == 4                      # handler, Circle.area, main, <module> - the test is not counted


def test_impact_ambiguous_target_returns_candidates(project):
    index(project, full=True, publish=False)
    r = Atlas(project).impact("area")
    assert r["error"] == "ambiguous" and len(r["candidates"]) == 2


def test_trace_and_missing_path(project):
    index(project, full=True, publish=False)
    atlas = Atlas(project)
    path = atlas.trace("main", "clamp")["path"]
    assert [p[0].split("::")[-1] for p in path] == ["main", "handler", "clamp"]
    assert atlas.trace("clamp", "main")["path"] == []


def test_search_matches_camel_and_snake_case(project):
    index(project, full=True, publish=False)
    names = {r["qname"] for r in Atlas(project).search("two sum")}
    assert "Solution.twoSum" in names
    assert "make_circle" in {r["qname"] for r in Atlas(project).search("makeCircle")}


def test_locate_maps_python_traceback_to_symbols(project):
    index(project, full=True, publish=False)
    tb = ('Traceback (most recent call last):\n  File "/x/app.py", line 20, in <module>\n    main()\n'
          '  File "/x/pkg/util.py", line 3, in clamp\n    return max(lo, min(x, hi))\nValueError: bad\n')
    r = Atlas(project).locate(tb)
    assert [f["symbol"].split("::")[-1] for f in r["frames"]] == ["clamp", "<module>"]   # innermost frame first
    assert r["focus"]["symbol"].endswith("::clamp")


def test_changes_without_git_uses_symbol_hashes(project):
    index(project, full=True, publish=False)
    write(project, {"pkg/util.py": (
        "def clamp(x, lo, hi):\n    return x\n\ndef brand_new():\n    return 2\n")})
    c = Atlas(project).changes()
    short = lambda ids: {i.split("::")[-1] for i in ids}
    assert short(c["modified"]) == {"clamp"} and short(c["added"]) == {"brand_new"} and short(c["removed"]) == {"unused_helper"}
    assert c["baseline"].startswith("last index")


def test_changes_with_git_compares_against_head(project):
    def git(*a):
        subprocess.run(["git", "-C", str(project), "-c", "user.email=t@t", "-c", "user.name=t", *a], check=True, capture_output=True)
    git("init", "-q")
    (project / ".gitignore").write_text("CodeAtlas/graph/\nCodeAtlas/*.html\nCodeAtlas/reports/\n")
    git("add", "-A")
    git("commit", "-qm", "base")
    index(project, full=True, publish=False)
    write(project, {"pkg/util.py": (
        "def clamp(x, lo, hi):\n    return min(hi, max(x, lo))\n\ndef unused_helper():\n    return 1\n")})
    c = Atlas(project).changes()
    assert c["git"] and c["baseline"] == "git HEAD"
    assert {i.split("::")[-1] for i in c["modified"]} == {"clamp"}
    assert not c["removed"] and not c["added"]


def test_concurrent_index_runs_are_serialised(project):
    import threading
    results = []
    threads = [threading.Thread(target=lambda: results.append(index(project, full=True, publish=False))) for _ in range(3)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(results) == 3 and len({r.symbols for r in results}) == 1


def test_codeatlasignore_excludes_paths_and_globs(project):
    write(project, {"tools/gen_pb2.py": "def f():\n    pass\n", "scratch/x.py": "def g():\n    pass\n",
                    ".codeatlasignore": "# generated code\n*_pb2.py\nscratch/\n"})
    index(project, full=True, publish=False)
    files = {r["path"] for r in Atlas(project).db.execute("SELECT path FROM files")}
    assert "tools/gen_pb2.py" not in files and "scratch/x.py" not in files and "app.py" in files


def test_unchanged_reindex_is_fast(project):
    index(project, full=True, publish=False)
    started = time.time()
    index(project, publish=False)
    assert time.time() - started < 2.0


def test_user_functions_named_like_builtins_are_not_ignored(project):
    write(project, {"helpers.py": "def format(x):\n    return str(x)\n\ndef show(v):\n    return format(v)\n\ndef plain(v):\n    return print(v)\n"})
    index(project, full=True, publish=False)
    e = edges(project)
    assert ("CALLS", "show", "format", "high") in e                 # the project's own `format` shadows the builtin
    assert not any(src == "plain" and dst == "print" for _t, src, dst, _c in e)   # the real builtin is still ignored
