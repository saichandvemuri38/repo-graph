import json
import threading
import urllib.error
import urllib.request

import pytest

from atlas_engine import projects
from atlas_engine.indexer import index
from atlas_engine.webserver import make_server


@pytest.fixture
def server(project, tmp_path, monkeypatch):
    monkeypatch.setenv("CODEATLAS_HOME", str(tmp_path / "home"))
    index(project, full=True, publish=False)
    srv, app = make_server(0, project)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]

    def call(path, body=None, method=None, token=True, host=None):
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode() if body is not None else None,
                                     method=method or ("POST" if body is not None else "GET"))
        if token:
            req.add_header("X-CodeAtlas-Token", app.token)
        if host:
            req.add_header("Host", host)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if r.headers["Content-Type"].startswith("application/json") else raw.decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    call.name = app.home_name
    call.app = app
    call.port = port
    yield call
    srv.shutdown()


def test_token_and_host_are_enforced(server):
    assert server("/api/projects", token=False)[0] == 401
    assert server("/api/projects", host="evil.example.com")[0] == 403
    status, data = server("/api/projects")
    assert status == 200 and data["home"] == server.name


def test_index_page_gets_a_per_run_token(server):
    status, page = server("/", token=False)
    assert status == 200 and server.app.token in page and "__TOKEN__" not in page


def test_static_files_cannot_escape_the_web_folder(server):
    assert server("/../pyproject.toml", token=False)[0] == 404
    assert server("/%2e%2e/pyproject.toml", token=False)[0] == 404


def test_graph_payload_has_folders_files_and_symbols(server):
    _, g = server(f"/api/p/{server.name}/graph")
    kinds = {n["k"] for n in g["nodes"]}
    assert {"folder", "file", "class", "function", "method"} <= kinds
    ids = {n["id"] for n in g["nodes"]}
    assert "dir:pkg" in ids and "pkg/util.py" in ids and "pkg/util.py::clamp" in ids
    assert all(e[0] in ids and e[1] in ids for e in g["edges"])                       # no dangling edges
    assert any(e[2] == "CONTAINS" for e in g["edges"]) and any(e[2] == "CALLS" for e in g["edges"])
    _, small = server(f"/api/p/{server.name}/graph?overview=1")
    assert {n["k"] for n in small["nodes"]} <= {"folder", "file"}


def test_search_syntax(server):
    def q(text):
        return server(f"/api/p/{server.name}/search?q={text.replace(' ', '%20').replace(':', '%3A')}")[1]["results"]
    assert q("clamp")[0]["label"] == "clamp"
    assert {r["kind"] for r in q("type:class")} == {"class"}
    assert all(r["file"].startswith("pkg/") for r in q("path:pkg/ area"))
    assert any(r["kind"] == "file" and r["id"] == "app.py" for r in q("app"))
    assert any(r["kind"] == "folder" for r in q("type:folder"))
    assert any(r["label"] == "Solution.twoSum" for r in q("two sum"))               # camelCase matches words


def test_node_detail_for_symbol_file_and_folder(server):
    n = lambda i: server(f"/api/p/{server.name}/node?id={i.replace(':', '%3A').replace('/', '%2F')}")[1]
    sym = n("pkg/util.py::clamp")
    assert sym["type"] == "symbol" and {c["src"].split("::")[-1] for c in sym["callers"]} == {"handler", "Circle.area"}
    f = n("pkg/util.py")
    assert f["type"] == "file" and "app.py" in f["imported_by"] and any(s["qname"] == "clamp" for s in f["symbols"])
    d = n("dir:pkg")
    assert d["type"] == "folder" and "shapes.py" in " ".join(d["files"])
    assert n("nope")["type"] == "missing"


def test_source_only_serves_indexed_files(server):
    ok = server(f"/api/p/{server.name}/source?file=pkg%2Futil.py")[1]
    assert ok["lines"][0].startswith("def clamp")
    assert "error" in server(f"/api/p/{server.name}/source?file=..%2Fetc%2Fpasswd")[1]
    assert "error" in server(f"/api/p/{server.name}/source?file=CodeAtlas%2Fgraph%2Fatlas.db")[1]


def test_sql_is_read_only(server):
    run = lambda sql: server(f"/api/p/{server.name}/sql", {"query": sql})[1]
    assert run("SELECT COUNT(*) FROM symbols")["rows"][0][0] > 5
    for bad in ("DELETE FROM symbols", "DROP TABLE symbols", "INSERT INTO meta VALUES ('a','b')",
                "SELECT 1; DELETE FROM symbols", "ATTACH DATABASE '/tmp/x.db' AS x", "PRAGMA writable_schema=1"):
        assert "error" in run(bad), bad
    assert run("SELECT COUNT(*) FROM symbols")["rows"][0][0] > 5                     # still intact
    assert "error" in run("WITH RECURSIVE t(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM t) SELECT COUNT(*) FROM t")   # time limited


def test_processes_clusters_and_flow_detail(server):
    procs = server(f"/api/p/{server.name}/processes")[1]["processes"]
    assert procs and {"cross", "steps_ids", "is_test"} <= set(procs[0])
    detail = server(f"/api/p/{server.name}/process?id={procs[0]['id']}")[1]
    assert detail["mermaid"].startswith("flowchart") and detail["depth"]
    assert server(f"/api/p/{server.name}/clusters")[1]["clusters"]


def test_impact_trace_changes_endpoints(server):
    imp = server(f"/api/p/{server.name}/impact?target=clamp")[1]
    assert imp["risk"] in ("LOW", "MEDIUM", "HIGH", "CRITICAL") and "1" in imp["levels"]
    tr = server(f"/api/p/{server.name}/trace?from=main&to=clamp")[1]
    assert [p[0].split("::")[-1] for p in tr["path"]] == ["main", "handler", "clamp"]
    assert "risk" in server(f"/api/p/{server.name}/changes")[1]


def test_add_project_indexes_it_outside_its_own_folder(server, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    (other / "a.py").write_text("def hello():\n    return 1\n")
    status, r = server("/api/projects", {"path": str(other)})
    assert status == 200
    import time
    for _ in range(50):
        job = server(f"/api/jobs/{r['job']}")[1]
        if job["status"] != "running":
            break
        time.sleep(0.1)
    assert job["status"] == "done"
    assert not (other / "CodeAtlas").exists()                                        # source folder untouched
    _, g = server(f"/api/p/{r['project']}/graph")
    assert any(n["id"] == "a.py::hello" for n in g["nodes"])
    assert server(f"/api/projects/{r['project']}", method="DELETE")[1]["removed"]
    assert server("/api/projects", {"path": str(tmp_path / "missing")})[0] == 400


def test_settings_never_return_the_key(server, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _, s = server("/api/settings", {"anthropic_api_key": "sk-ant-secret-1234567890", "model": "claude-opus-5"})
    assert s["has_key"] and "secret" not in json.dumps(s) and s["key_hint"] == "…7890" and s["model"] == "claude-opus-5"


# ---------------------------------------------------------------- AI chat against a fake Claude API

class FakeClaude:
    """Speaks just enough of POST /v1/messages: asks for a tool on the first turn, answers on the second."""

    def __init__(self, script):
        import http.server
        self.requests, self.script = [], list(script)
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a): pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append({"headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})
                code, payload = outer.script.pop(0)
                raw = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"


def _stream_via_helper(server, messages):
    req = urllib.request.Request(f"http://127.0.0.1:{server.port}/api/p/{server.name}/chat",
                                 data=json.dumps({"messages": messages}).encode(), method="POST",
                                 headers={"X-CodeAtlas-Token": server.app.token})
    with urllib.request.urlopen(req, timeout=20) as r:
        return [json.loads(line) for line in r.read().decode().splitlines() if line.strip()]


def test_chat_runs_the_tool_loop_and_streams_events(server, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
    fake = FakeClaude([
        (200, {"stop_reason": "tool_use", "content": [
            {"type": "text", "text": "Let me look."},
            {"type": "tool_use", "id": "t1", "name": "get_symbol", "input": {"symbol": "clamp"}}]}),
        (200, {"stop_reason": "end_turn", "content": [{"type": "text", "text": "clamp is called by handler (app.py:11)."}]}),
    ])
    monkeypatch.setenv("ANTHROPIC_BASE_URL", fake.url)
    events = _stream_via_helper(server, [{"role": "user", "content": "who calls clamp?"}])
    kinds = [e["type"] for e in events]
    assert kinds == ["text", "tool_start", "tool_result", "text", "done"]
    assert events[1]["name"] == "get_symbol" and events[2]["ok"]
    assert "handler" in fake.requests[1]["body"]["messages"][-1]["content"][0]["content"]       # tool result reached the model
    assert fake.requests[0]["headers"]["x-api-key"] == "sk-test-key"
    assert fake.requests[0]["body"]["tools"] and fake.requests[0]["body"]["system"]
    cites = events[-1]["citations"]
    assert any(c["file"] == "pkg/util.py" for c in cites) and any(c["file"] == "app.py" and c["start"] == 11 for c in cites)


def test_chat_reports_api_errors_and_missing_key(server, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("atlas_engine.chat.load_settings", lambda: {})
    ev = _stream_via_helper(server, [{"role": "user", "content": "hi"}])
    assert ev[0]["type"] == "error" and ev[0]["code"] == "no_key"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    fake = FakeClaude([(401, {"error": {"message": "invalid x-api-key"}})])
    monkeypatch.setenv("ANTHROPIC_BASE_URL", fake.url)
    ev = _stream_via_helper(server, [{"role": "user", "content": "hi"}])
    assert ev[0]["type"] == "error" and "401" in ev[0]["message"] and "invalid x-api-key" in ev[0]["message"]


def test_chat_survives_a_failing_tool(server, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    fake = FakeClaude([
        (200, {"stop_reason": "tool_use", "content": [{"type": "tool_use", "id": "t1", "name": "run_sql", "input": {"query": "DROP TABLE symbols"}}]}),
        (200, {"stop_reason": "end_turn", "content": [{"type": "text", "text": "That query is not allowed."}]}),
    ])
    monkeypatch.setenv("ANTHROPIC_BASE_URL", fake.url)
    ev = _stream_via_helper(server, [{"role": "user", "content": "drop it"}])
    assert [e["type"] for e in ev][-1] == "done"
    assert "Only SELECT" in fake.requests[1]["body"]["messages"][-1]["content"][0]["content"]
