import base64
import gzip
import json
import re
import shutil
import subprocess
import threading
import urllib.error
import urllib.request

import pytest

from atlas_engine import cli, static_report
from atlas_engine.indexer import index
from atlas_engine.static_server import make_server

from conftest import write


def unpack(html: str) -> dict:
    blob = re.search(r'<script id="atlas-data" type="application/octet-stream">(.*?)</script>', html, re.S).group(1)
    return json.loads(gzip.decompress(base64.b64decode(blob)))


@pytest.fixture
def report(project):
    index(project, full=True)
    path = static_report.build(project)
    return path, path.read_text(encoding="utf-8")


def test_the_report_is_one_file_with_no_external_resources(project, report):
    path, html = report
    assert path == project / ".claude" / "atlas" / "report" / "index.html"
    assert html.startswith("<!doctype html>") and html.count('<script id="atlas-data"') == 1
    assert not re.findall(r'(?:src|href)="(?:https?:)?//', html)          # nothing is loaded from the network
    assert not re.findall(r'(?:src|href)="/', html)                        # and no site-relative files either
    assert "sigma" in html.lower() and "forceAtlas2" in html                # the drawing libraries are inline


def test_the_embedded_data_covers_the_graph_and_the_source(report):
    data = unpack(report[1])
    nodes = {n["id"]: n for n in data["graph"]["nodes"]}
    assert {"folder", "file", "class", "function", "method"} <= {n["k"] for n in nodes.values()}
    assert "pkg/util.py::clamp" in nodes and "pkg/util.py" in nodes and "dir:pkg" in nodes
    calls = {(d[0], d[1], d[2]) for d in data["deps"]}
    assert any(s.endswith("::clamp") or d.endswith("::clamp") for s, d, _ in calls)
    assert data["sources"]["pkg/util.py"]["lines"][0].startswith("def clamp")
    assert data["symx"]["pkg/util.py::clamp"]["sig"].startswith("def clamp")
    assert data["projects"]["home"] == data["status"]["name"] and data["status"]["stale_count"] == 0
    assert {"unused", "taint", "changes", "clusters", "processes"} <= set(data)
    assert any(u["symbol"]["name"] == "unused_helper" for u in data["unused"]["unreferenced"])
    assert any(r[1] == "clamp" for r in data["search"])


def test_source_is_capped_and_the_report_says_so(project, monkeypatch):
    index(project, full=True)
    monkeypatch.setattr(static_report, "MAX_SOURCE_BYTES", 40)
    data = unpack(static_report.build(project).read_text(encoding="utf-8"))
    assert len(data["sources"]) < len(data["graph"]["nodes"])                # some files were left out, none crash the page


def test_freshness_follows_the_graph(project):
    index(project, full=True)
    assert not static_report.is_current(project)                             # nothing built yet
    static_report.build(project)
    assert static_report.is_current(project)
    write(project, {"pkg/extra.py": "def more():\n    return 1\n"})
    index(project)
    assert not static_report.is_current(project)                             # the graph moved on


def test_the_javascript_bundle_is_a_classic_script(tmp_path):
    js = static_report.bundle_js()
    assert not re.search(r"^\s*(import|export)\s", js, re.M)
    assert js.count("const __m_") == len(static_report.JS_ORDER) and "__m_static_api" in js
    node = shutil.which("node")
    if node:
        target = tmp_path / "bundle.js"
        target.write_text(js, encoding="utf-8")
        check = subprocess.run([node, "--check", str(target)], capture_output=True, text=True)
        assert check.returncode == 0, check.stderr


def test_localhost_serves_that_one_file_and_nothing_else(report):
    path, html = report
    server = make_server(path, 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]

    def get(target, host=None):
        req = urllib.request.Request(f"http://127.0.0.1:{port}{target}")
        if host:
            req.add_header("Host", host)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, b""
    try:
        status, body = get("/")
        assert status == 200 and body.decode() == html
        assert get("/index.html")[0] == 200
        assert get("/favicon.ico")[0] == 204
        assert get("/api/projects")[0] == 404 and get("/../etc/passwd")[0] == 404
        assert get("/", host="evil.example.com")[0] == 403                    # DNS rebinding
    finally:
        server.shutdown()
        server.server_close()


def test_report_and_web_commands(project, capsys):
    assert cli.main(["--root", str(project), "report"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].endswith("index.html") and out[1].startswith("file://")
    assert cli.main(["--root", str(project), "--no-refresh", "web", "--no-server"]) == 0
    assert capsys.readouterr().out.splitlines()[0] == out[0]
