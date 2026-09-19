import pytest

from atlas_engine.parsers import available_languages
from atlas_engine.indexer import index
from atlas_engine.queries import Atlas

from conftest import write

pytestmark = pytest.mark.skipif("java" not in available_languages(), reason="tree-sitter-language-pack not installed")


def graph(root):
    index(root, full=True, publish=False)
    atlas = Atlas(root)
    rows = atlas.db.execute("SELECT type, src, dst, conf FROM edges WHERE type != 'DEFINES'").fetchall()
    return {(r["type"], r["src"].split("::")[-1], r["dst"].split("::")[-1], r["conf"]) for r in rows}


def test_javascript_and_typescript(tmp_path):
    (tmp_path / "CodeAtlas").mkdir()
    write(tmp_path, {
        "web/math.js": "// Adds.\nexport function add(a, b) { return a + b; }\nexport class Calc {\n  push(x) { return add(1, x); }\n  result() { return 1; }\n}\n",
        "web/main.js": 'import { add, Calc } from "./math";\nfunction run() {\n  const c = new Calc();\n  c.push(1);\n  return add(1, 2) + c.result();\n}\nrun();\n',
        "web/view.tsx": 'import { Calc } from "./math";\nclass Base { hi() { return 1; } }\nclass Kid extends Base { go(c: Calc) { c.result(); return this.hi(); } }\nfunction Panel() { return null; }\nexport function View() { return <Panel />; }\n',
    })
    e = graph(tmp_path)
    assert ("CALLS", "run", "add", "high") in e
    assert ("CALLS", "run", "Calc.push", "med") in e
    assert ("CALLS", "Calc.push", "add", "high") in e
    assert ("IMPORTS", "web/main.js", "web/math.js", "high") in e
    assert ("EXTENDS", "Kid", "Base", "high") in e
    assert ("CALLS", "Kid.go", "Calc.result", "med") in e
    assert ("CALLS", "Kid.go", "Base.hi", "high") in e
    assert ("CALLS", "View", "Panel", "high") in e
    assert Atlas(tmp_path).db.execute("SELECT summary FROM symbols WHERE name='add'").fetchone()[0] == "Adds."


def test_java_field_and_static_types_and_imports(tmp_path):
    (tmp_path / "CodeAtlas").mkdir()
    write(tmp_path, {
        "src/repo/UserRepo.java": "package repo;\npublic class UserRepo {\n  public void save(String n) { check(n); }\n  private void check(String n) {}\n}\n",
        "src/app/App.java": "package app;\nimport repo.UserRepo;\npublic class App {\n  private UserRepo repo = new UserRepo();\n  public void run() { repo.save(\"x\"); helper(); }\n  void helper() {}\n  public static void main(String[] a) { new App().run(); }\n}\n",
    })
    e = graph(tmp_path)
    assert ("CALLS", "UserRepo.save", "UserRepo.check", "high") in e
    assert ("CALLS", "App.run", "UserRepo.save", "med") in e
    assert ("CALLS", "App.run", "App.helper", "high") in e
    assert ("CALLS", "App.main", "App.run", "med") in e
    assert ("IMPORTS", "src/app/App.java", "src/repo/UserRepo.java", "high") in e


def test_go_same_package_files_share_a_namespace(tmp_path):
    (tmp_path / "CodeAtlas").mkdir()
    write(tmp_path, {
        "svc/store.go": "package svc\n\ntype Store struct{}\n\nfunc (s *Store) Add(x string) { s.log(x) }\nfunc (s *Store) log(x string) {}\nfunc NewStore() *Store { return &Store{} }\n",
        "svc/main.go": "package svc\n\nfunc Run() {\n\tst := NewStore()\n\tst.Add(\"a\")\n\thelper(st)\n}\nfunc helper(s *Store) { s.Add(\"b\") }\n",
    })
    e = graph(tmp_path)
    assert ("CALLS", "Run", "NewStore", "high") in e
    assert ("CALLS", "Store.Add", "Store.log", "med") in e
    assert ("CALLS", "helper", "Store.Add", "med") in e


def test_workspace_packages_tsconfig_aliases_wrapped_components_and_default_imports(tmp_path):
    (tmp_path / "CodeAtlas").mkdir()
    write(tmp_path, {
        "packages/ui/package.json": '{"name": "@repo/ui", "exports": {"./*": "./src/*.tsx", "./components/*": "./src/components/*.tsx"}}',
        "packages/ui/src/button.tsx": "import * as React from 'react';\nexport const Button = React.forwardRef((props, ref) => {\n  return <button ref={ref} />;\n});\n",
        "packages/ui/src/card.tsx": "export default function Card() {\n  return <div />;\n}\n",
        "packages/ui/src/components/NavBar.tsx": "export function NavBar() { return <nav />; }\n",
        # tsconfig with comments and a trailing comma, as create-next-app generates them
        "apps/web/tsconfig.json": '{\n  // path aliases\n  "compilerOptions": { "paths": { "@/*": ["./*"], }, },\n}\n',
        "apps/web/lib/format.ts": "export function fmt(x) { return String(x); }\n",
        "apps/web/app/page.tsx": (
            "import { Button } from '@repo/ui/button';\nimport Card from '@repo/ui/card';\n"
            "import { NavBar } from '@repo/ui/components/NavBar';\nimport { fmt } from '@/lib/format';\n"
            "export default function Home() {\n  fmt(1);\n  return <div><Button /><Card /><NavBar /></div>;\n}\n"),
    })
    e = graph(tmp_path)
    assert ("CALLS", "Home", "Button", "high") in e          # workspace exports, and a forwardRef-wrapped component
    assert ("CALLS", "Home", "Card", "high") in e            # default import
    assert ("CALLS", "Home", "NavBar", "high") in e          # exports pattern with a sub-folder
    assert ("CALLS", "Home", "fmt", "high") in e             # tsconfig paths alias
    assert ("IMPORTS", "apps/web/app/page.tsx", "packages/ui/src/button.tsx", "high") in e


def test_typescript_return_types_callback_params_and_builtin_noise(tmp_path):
    (tmp_path / "CodeAtlas").mkdir()
    write(tmp_path, {
        "graph.ts": "export class Graph {\n  addNode(n: string) {}\n}\nexport function createGraph(): Graph {\n  return new Graph();\n}\n",
        "use.ts": (
            "import { createGraph } from './graph';\n"
            "export function build(items: string[]) {\n"
            "  const g = createGraph();\n"
            "  items.forEach((item) => { g.addNode(item); });\n"
            "  new Promise((resolve, reject) => { resolve(1); reject(2); });\n"
            "  console.log('done', JSON.parse('{}'));\n"
            "}\n"),
    })
    e = graph(tmp_path)
    assert ("CALLS", "build", "Graph.addNode", "med") in e          # g typed from createGraph()'s `: Graph`, inside a callback
    assert ("CALLS", "build", "createGraph", "high") in e
    names = {r["name"] for r in Atlas(tmp_path).db.execute("SELECT name FROM unresolved")}
    assert not names & {"resolve", "reject", "console.log", "JSON.parse"}   # callback params and library calls are not graph gaps


def test_skipped_files_are_reported_not_silent(project):
    from atlas_engine import config
    write(project, {"big_generated.py": "x = 1\n" * 10})
    old = config.MAX_FILE_BYTES
    import atlas_engine.indexer as idx
    idx.MAX_FILE_BYTES = 20
    try:
        result = index(project, full=True, publish=False)
    finally:
        idx.MAX_FILE_BYTES = old
    assert any(p == "big_generated.py" and "larger" in why for p, why in result.skipped)
    assert int(Atlas(project).store.meta()["skippedCount"]) >= 1
