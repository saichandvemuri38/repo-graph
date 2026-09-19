"""Local web server for the CodeAtlas explorer: static UI + JSON API. Standard library only.

Security model (this serves your source code, so it is strict):
  * binds to 127.0.0.1 only;
  * the Host header must be localhost or 127.0.0.1 (blocks DNS-rebinding);
  * every /api request needs a random per-run token that only the served page knows (blocks other websites);
  * the SQL console is read-only at the SQLite level; source viewing is limited to files in the index.
"""

from __future__ import annotations

import json
import mimetypes
import re
import secrets
import threading
import time
import urllib.parse
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import chat, projects
from .indexer import index
from .webapi import WebAPI

WEB_DIR = Path(__file__).parent / "web"


class App:
    def __init__(self, home: Path | None):
        self.token = secrets.token_urlsafe(24)
        self.apis: dict[str, WebAPI] = {}
        self.locks: dict[str, threading.RLock] = {}
        self.jobs: dict[str, dict] = {}
        self.guard = threading.Lock()
        self.home_name = None
        if home is not None:
            self.home_name = projects.register(home, external=False).name

    def api(self, name: str) -> tuple[WebAPI, threading.RLock] | None:
        with self.guard:
            if name not in self.apis:
                proj = projects.get(name)
                if not proj:
                    return None
                self.apis[name] = WebAPI(proj)
                self.locks[name] = threading.RLock()
            return self.apis[name], self.locks[name]

    def drop(self, name: str) -> None:
        with self.guard:
            api = self.apis.pop(name, None)
            self.locks.pop(name, None)
        if api:
            api.close()

    def start_job(self, proj: projects.Project, full: bool) -> str:
        job_id = uuid.uuid4().hex[:10]
        job = {"id": job_id, "project": proj.name, "status": "running", "message": "Scanning files…", "parsed": 0, "started": time.time()}
        self.jobs[job_id] = job

        def log(msg: str) -> None:
            if msg.startswith("parsed "):
                job["parsed"] += 1
                job["message"] = f"Parsing {msg[7:]}"

        def work() -> None:
            try:
                result = index(proj.root, full=full, log=log, data_dir=proj.data_dir)
                job.update(status="done", message=result.summary(), summary=result.summary(), skipped=result.skipped[:10],
                           parse_errors=result.parse_errors[:10])
            except Exception as exc:
                job.update(status="error", message=f"{type(exc).__name__}: {exc}")
            finally:
                job["finished"] = time.time()
                self.drop(proj.name)           # reopen on next request so the fresh database is used

        threading.Thread(target=work, daemon=True).start()
        return job_id


class Handler(BaseHTTPRequestHandler):
    server_version = "CodeAtlas"
    app: App
    port: int

    def log_message(self, fmt, *args):     # keep the terminal quiet
        pass

    # ------------------------------------------------------------------ helpers

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").lower()
        return host in (f"localhost:{self.port}", f"127.0.0.1:{self.port}", "localhost", "127.0.0.1")

    def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8", extra: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data, code: int = 200) -> None:
        self._send(code, json.dumps(data, default=str).encode())

    def _err(self, code: int, message: str) -> None:
        self._json({"error": message}, code)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n > 2_000_000:
            raise ValueError("body too large")
        return json.loads(self.rfile.read(n) or b"{}")

    def _authorised(self) -> bool:
        return secrets.compare_digest(self.headers.get("X-CodeAtlas-Token") or "", self.app.token)

    # ------------------------------------------------------------------ routing

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def _dispatch(self, method: str) -> None:
        if not self._host_ok():
            return self._err(403, "Forbidden host")
        url = urllib.parse.urlparse(self.path)
        query = {k: v[-1] for k, v in urllib.parse.parse_qs(url.query).items()}
        path = urllib.parse.unquote(url.path)
        try:
            if path.startswith("/api/"):
                if not self._authorised():
                    return self._err(401, "Missing or wrong token. Reload the page.")
                return self._api(method, path[5:], query)
            if method != "GET":
                return self._err(405, "Method not allowed")
            return self._static(path)
        except (BrokenPipeError, ConnectionResetError):
            return
        except ValueError as exc:
            return self._err(400, str(exc))
        except Exception as exc:                        # never leak a traceback to the browser
            return self._err(500, f"{type(exc).__name__}: {exc}")

    def _static(self, path: str) -> None:
        if path == "/favicon.ico":
            svg = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><text y="26" font-size="28" fill="#4f46e5">\xe2\x97\x88</text></svg>'
            return self._send(200, svg, "image/svg+xml")
        rel = "index.html" if path in ("", "/") else path.lstrip("/")
        full = (WEB_DIR / rel).resolve()
        if WEB_DIR.resolve() not in full.parents or not full.is_file():
            return self._err(404, "Not found")
        data = full.read_bytes()
        if rel == "index.html":
            data = data.replace(b"__TOKEN__", self.app.token.encode())
        ctype = mimetypes.guess_type(str(full))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        self._send(200, data, ctype)

    # ------------------------------------------------------------------ API

    def _api(self, method: str, path: str, q: dict) -> None:
        parts = [p for p in path.split("/") if p]
        if parts == ["projects"]:
            if method == "GET":
                return self._json({"projects": self._project_list(), "home": self.app.home_name})
            if method == "POST":
                return self._add_project(self._body())
        if len(parts) == 2 and parts[0] == "projects" and method == "DELETE":
            ok = projects.remove(parts[1])
            self.app.drop(parts[1])
            return self._json({"removed": ok})
        if len(parts) == 3 and parts[0] == "projects" and parts[2] == "reindex" and method == "POST":
            proj = projects.get(parts[1])
            if not proj:
                return self._err(404, "Unknown project")
            return self._json({"job": self.app.start_job(proj, bool(self._body().get("full")))})
        if len(parts) == 2 and parts[0] == "jobs" and method == "GET":
            job = self.app.jobs.get(parts[1])
            return self._json(job) if job else self._err(404, "Unknown job")
        if parts == ["settings"]:
            if method == "POST":
                body = self._body()
                chat.save_settings({k: body[k] for k in ("anthropic_api_key", "model") if k in body and isinstance(body[k], str)})
            return self._json(chat.public_settings())
        if len(parts) >= 3 and parts[0] == "p":
            found = self.app.api(parts[1])
            if not found:
                return self._err(404, f"Unknown project '{parts[1]}'")
            api, lock = found
            if parts[2] == "chat" and method == "POST":
                return self._chat(api, lock)
            with lock:
                return self._project_api(api, method, parts[2], q)
        return self._err(404, "Unknown endpoint")

    def _project_list(self) -> list[dict]:
        return [{"name": p.name, "root": str(p.root), "external": p.data_dir is not None, "stats": p.stats()} for p in projects.list_projects()]

    def _add_project(self, body: dict) -> None:
        raw = str(body.get("path", "")).strip()
        if not raw:
            raise ValueError("Enter a folder path")
        root = Path(raw).expanduser()
        if not root.is_dir():
            raise ValueError(f"Not a folder: {root}")
        proj = projects.register(root, body.get("name") or None)
        return self._json({"project": proj.name, "job": self.app.start_job(proj, False)})

    def _project_api(self, api: WebAPI, method: str, what: str, q: dict) -> None:
        if what == "status":
            if q.get("refresh") == "1":
                api.atlas.refresh(min_interval=3.0)
            return self._json(api.status())
        if what == "graph":
            return self._json(api.graph(limit=int(q.get("limit") or 20000), overview=q.get("overview") == "1"))
        if what == "search":
            return self._json({"results": api.search(q.get("q", ""), int(q.get("limit") or 30))})
        if what == "node":
            return self._json(api.node(q.get("id", "")))
        if what == "source":
            return self._json(api.source(q.get("file", "")))
        if what == "clusters":
            return self._json({"clusters": api.clusters()})
        if what == "processes":
            return self._json({"processes": api.processes()})
        if what == "process":
            return self._json(api.process(q.get("id", "")))
        if what == "impact":
            return self._json(api.impact(q.get("target", ""), q.get("direction", "upstream")))
        if what == "trace":
            return self._json(api.trace(q.get("from", ""), q.get("to", "")))
        if what == "changes":
            return self._json(api.changes(q.get("base")))
        if what == "schema":
            return self._json(api.schema())
        if what == "sql" and method == "POST":
            return self._json(api.sql(str(self._body().get("query", ""))))
        if what == "locate" and method == "POST":
            return self._json(api.locate(str(self._body().get("text", ""))))
        return self._err(404, "Unknown endpoint")

    def _chat(self, api: WebAPI, lock: threading.RLock) -> None:
        body = self._body()
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        def emit(event: dict) -> None:
            self.wfile.write((json.dumps(event) + "\n").encode())
            self.wfile.flush()

        try:
            chat.run_chat(api, body.get("messages") or [], emit, lock)
        except (BrokenPipeError, ConnectionResetError):
            pass                                        # the user pressed Stop or closed the tab
        self.close_connection = True


def make_server(port: int = 4848, home: Path | None = None) -> tuple[ThreadingHTTPServer, App]:
    app = App(home)

    class Bound(Handler):
        pass

    Bound.app = app
    server = ThreadingHTTPServer(("127.0.0.1", port), Bound)
    server.daemon_threads = True
    Bound.port = server.server_address[1]
    return server, app


def serve_web(port: int, home: Path | None, open_browser: bool = False, project: str | None = None) -> None:
    server, app = make_server(port, home)
    name = project or app.home_name or ""
    url = f"http://localhost:{server.server_address[1]}/" + (f"?project={urllib.parse.quote(name)}" if name else "")
    print(f"CodeAtlas explorer: {url}")
    print("Press Ctrl+C to stop. The server listens on 127.0.0.1 only.")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
