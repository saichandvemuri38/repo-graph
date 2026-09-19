import pytest

from atlas_engine import reports
from atlas_engine.indexer import index
from atlas_engine.queries import Atlas

from conftest import write


def analyse(tmp_path, files, **kw):
    (tmp_path / ".claude" / "atlas").mkdir(parents=True)
    write(tmp_path, files)
    index(tmp_path, full=True)
    atlas = Atlas(tmp_path)
    return atlas, atlas.taint(**kw)


def kinds(result):
    return sorted((f["kind"], f["function"].split("::")[-1], f["line"]) for f in result["findings"])


def test_direct_flow_from_input_to_shell(tmp_path):
    _, r = analyse(tmp_path, {"m.py": 'import os\n\ndef go():\n    name = input("who? ")\n    cmd = "echo " + name\n    os.system(cmd)\n'})
    (f,) = r["findings"]
    assert (f["kind"], f["severity"], f["cwe"], f["confidence"], f["line"]) == ("command-exec", "HIGH", "CWE-78", "high", 6)
    assert [s[0] for s in f["steps"]] == [4, 4, 5, 6]         # source, assigned to name, assigned to cmd, sink
    assert "user input" in f["source"]


def test_fstring_sql_and_placeholder_safe(tmp_path):
    _, r = analyse(tmp_path, {"m.py": (
        "import sqlite3\n\ndef bad(cur, x):\n    n = input()\n    cur.execute(f\"select * from t where a = '{n}'\")\n\n"
        "def good(cur):\n    n = input()\n    cur.execute(\"select * from t where a = ?\", (n,))\n")})
    assert kinds(r) == [("sql", "bad", 5)]


def test_sanitisers_and_strong_updates_remove_findings(tmp_path):
    _, r = analyse(tmp_path, {"m.py": (
        "import os, shlex\n\ndef a():\n    x = input()\n    os.system('echo ' + shlex.quote(x))\n\n"
        "def b():\n    x = input()\n    n = int(x)\n    os.system('sleep %d' % n)\n\n"
        "def c():\n    x = input()\n    x = 'safe'\n    os.system(x)\n\n"
        "def d():\n    x = input()\n    os.system(shlex.quote(x))\n    os.system(x)\n")})
    assert kinds(r) == [("command-exec", "d", 20)]


def test_branches_join_and_loops_carry_taint(tmp_path):
    _, r = analyse(tmp_path, {"m.py": (
        "import os\n\ndef a(flag):\n    if flag:\n        x = input()\n    else:\n        x = 'ok'\n    os.system(x)\n\n"
        "def b():\n    parts = []\n    for i in range(3):\n        parts.append(input())\n    os.system(' '.join(parts))\n")})
    assert kinds(r) == [("command-exec", "a", 8), ("command-exec", "b", 14)]


def test_flow_crosses_function_calls_in_both_directions(tmp_path):
    atlas, r = analyse(tmp_path, {"m.py": (
        "import os\n\ndef run(cmd):\n    os.system(cmd)\n\ndef read():\n    return input()\n\n"
        "def main():\n    run(input())\n\ndef other():\n    eval(read())\n\ndef safe():\n    run('ls')\n")})
    got = {(f["kind"], f["function"].split("::")[-1], f["source_function"].split("::")[-1]) for f in r["findings"]}
    assert ("command-exec", "run", "main") in got            # argument taints a callee's sink
    assert ("code-exec", "other", "other") in got            # a callee's return value carries the source
    assert not any(f["source_function"].endswith("safe") for f in r["findings"])
    via = next(f for f in r["findings"] if f["source_function"].endswith("main"))
    notes = " ".join(s[2] for s in via["steps"])
    assert "passed to run() as `cmd`" in notes and via["callers"] == [] and "sink" in notes


def test_http_handler_parameters_and_request_data(tmp_path):
    _, r = analyse(tmp_path, {"app.py": (
        "import os, subprocess\nfrom flask import request\n\n@app.route('/x')\ndef view(name):\n    os.system(name)\n\n"
        "def other():\n    q = request.args.get('q')\n    subprocess.run(q, shell=True)\n\n"
        "def list_form():\n    subprocess.run(['ls', request.args['d']])\n")})
    assert kinds(r) == [("command-exec", "other", 10), ("command-exec", "view", 6)]      # list form without a shell is not flagged
    assert {f["category"] for f in r["findings"]} == {"http"}


def test_self_attributes_module_scripts_and_cli_files(tmp_path):
    _, r = analyse(tmp_path, {"m.py": (
        "import os, sys\n\nclass A:\n    def go(self):\n        self.cmd = input()\n        os.system(self.cmd)\n\n"
        "target = sys.argv[1]\nopen(target)\nos.system(target)\n")})
    assert kinds(r) == [("command-exec", "<module>", 10), ("command-exec", "A.go", 6)]      # argv into open() is normal for a CLI


def test_scope_report_and_reaching_definitions(tmp_path):
    atlas, r = analyse(tmp_path, {"m.py": (
        "import os\n\ndef a():\n    x = 'one'\n    if input():\n        x = input()\n    os.system(x)\n\n"
        "def b():\n    os.system('ls')\n")})
    scoped = atlas.taint("m.py::b")
    assert scoped["total"] == 0 and atlas.taint("m.py::a")["total"] == 1
    md = reports.md_taint(r)
    assert "Command injection (CWE-78)" in md and "**Fix:**" in md and "| line | code | what happens |" in md
    d = atlas.defs("a", "x")
    (use,) = d["uses"]
    assert use["line"] == 7 and [x["line"] for x in use["defs"]] == [4, 6]      # both assignments can reach the sink
    assert "reaching assignments" in reports.md_defs(d)
    assert "Nothing found" in reports.md_taint(atlas.taint("m.py::b"))


def test_tests_are_skipped_by_default(tmp_path):
    _, r = analyse(tmp_path, {"tests/test_x.py": "import os\n\ndef test_a():\n    os.system(input())\n"})
    assert r["findings"] == []
    (tmp_path / ".claude" / "atlas").mkdir(parents=True, exist_ok=True)
    assert len(Atlas(tmp_path).taint(include_tests=True)["findings"]) == 1


def test_files_that_do_not_parse_are_reported_not_analysed(tmp_path):
    _, r = analyse(tmp_path, {"bad.py": "def f(:\n    pass\n", "ok.py": "import os\nos.system(input())\n"})
    assert len(r["findings"]) == 1
    assert any(p == "bad.py" for p, _ in r["skipped"])
