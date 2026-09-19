from atlas_engine.parsers import parse_file


def parse(src: str, path: str = "m.py"):
    return parse_file(path, "python", src, "abc123abc123")


def ids(fp):
    return {s.qname: s for s in fp.symbols}


def targets(fp, type_):
    return {(e.src.split("::")[1], e.target) for e in fp.edges if e.type == type_}


def test_symbols_have_exact_line_ranges_and_docstring_summary():
    fp = parse('def f(a):\n    """Do f."""\n    return a\n\n\nclass C:\n    def m(self):\n        pass\n')
    s = ids(fp)
    assert (s["f"].start, s["f"].end, s["f"].kind) == (1, 3, "function")
    assert s["f"].summary == "Do f."
    assert (s["C"].start, s["C"].end) == (6, 8)
    assert (s["C.m"].kind, s["C.m"].start, s["C.m"].end) == ("method", 7, 8)


def test_decorators_are_included_in_the_range_and_referenced():
    fp = parse("import functools\n\n@functools.lru_cache\ndef f():\n    pass\n")
    assert ids(fp)["f"].start == 3
    assert ("f", "functools.lru_cache") in targets(fp, "REFERENCES")


def test_calls_are_attributed_to_the_innermost_definition():
    fp = parse("def outer():\n    def inner():\n        helper()\n    inner()\n")
    assert ("outer.inner", "helper") in targets(fp, "CALLS")
    assert ("outer", "inner") in targets(fp, "CALLS")


def test_self_calls_and_super():
    fp = parse("class A:\n    def a(self):\n        self.b()\n        super().c()\n    def b(self):\n        pass\n")
    calls = targets(fp, "CALLS")
    assert ("A.a", "self.b") in calls and ("A.a", "super.c") in calls


def test_receiver_type_inferred_from_constructor_annotation_and_attribute():
    fp = parse(
        "class S:\n    def __init__(self):\n        self.repo = Repo()\n    def go(self, x: Item):\n"
        "        y = Widget()\n        y.spin()\n        x.use()\n        self.repo.save()\n"
    )
    calls = {(t, e.inferred) for (s, t), e in ((k, next(e for e in fp.edges if (e.src.split('::')[1], e.target) == k)) for k in targets(fp, "CALLS"))}
    assert ("Widget.spin", True) in calls
    assert ("Item.use", True) in calls
    assert ("Repo.save", True) in calls


def test_local_variables_are_not_mistaken_for_functions():
    fp = parse("def f(cb):\n    result = compute()\n    cb()\n    result.go()\n")
    by_target = {e.target: e for e in fp.edges if e.type == "CALLS"}
    assert by_target["cb"].local                                   # a parameter, never a global function
    assert by_target["@ret:compute:go"].inferred                   # typed later from compute()'s return annotation
    assert not by_target["compute"].local


def test_module_symbol_only_when_top_level_code_does_something():
    assert "<module>" not in ids(parse("def f():\n    pass\n"))
    fp = parse("def main():\n    pass\n\nif __name__ == '__main__':\n    main()\n")
    mod = ids(fp)["<module>"]
    assert "entry" in mod.summary.lower()
    assert ("<module>", "main") in targets(fp, "CALLS")


def test_imports_produce_bindings_and_edges():
    fp = parse("import a.b\nimport c as d\nfrom e import f as g\nfrom . import h\nfrom .i import *\n")
    b = {x.alias: (x.module, x.name, x.level) for x in fp.imports}
    assert b["a"] == ("a", None, 0) and b["d"] == ("c", None, 0)
    assert b["g"] == ("e", "f", 0) and b["h"] == ("", "h", 1) and b["*"] == ("i", "*", 1)
    assert {e.target for e in fp.edges if e.type == "IMPORTS"} == {"a.b", "c", "e", "", "i"}


def test_syntax_error_falls_back_to_a_tolerant_scan():
    fp = parse("class Solution:\n    def twoSum(self, nums, target):\n", "day-1.py")
    assert fp.error.startswith("IndentationError")
    s = ids(fp)
    assert s["Solution"].kind == "class" and s["Solution.twoSum"].kind == "method"
    assert not [e for e in fp.edges if e.type == "CALLS"]


def test_redefinitions_get_unique_ids():
    fp = parse("def f():\n    pass\n\ndef f():\n    pass\n")
    assert {"f", "f#2"} <= set(ids(fp))


def test_symbol_bodies_are_hashed_and_change_with_edits():
    a = ids(parse("def f():\n    return 1\n\ndef g():\n    return 2\n"))
    b = ids(parse("def f():\n    return 1\n\ndef g():\n    return 3\n"))
    assert a["f"].body_sha == b["f"].body_sha
    assert a["g"].body_sha != b["g"].body_sha
