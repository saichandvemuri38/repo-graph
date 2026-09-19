"""Serves the one-file HTML report on localhost. It serves that file and nothing else."""

from __future__ import annotations

import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def make_server(report: Path, port: int = 4848) -> ThreadingHTTPServer:
    """Bind 127.0.0.1 only. If the port is taken, try the next ones."""

    class Handler(BaseHTTPRequestHandler):
        server_version = "CodeAtlasReport"

        def log_message(self, *_args) -> None:        # quiet
            pass

        def _send(self, code: int, body: bytes, kind: str = "text/plain; charset=utf-8") -> None:
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
            if host not in ("localhost", "127.0.0.1"):           # blocks DNS-rebinding
                return self._send(403, b"Forbidden")
            if self.path.split("?", 1)[0] == "/favicon.ico":
                return self._send(204, b"")
            if self.path.split("?", 1)[0] not in ("/", "/index.html"):
                return self._send(404, b"Not found")
            try:
                self._send(200, report.read_bytes(), "text/html; charset=utf-8")
            except OSError:
                self._send(404, b"The report has not been generated yet.")

        do_HEAD = do_GET

    last: OSError | None = None
    for candidate in range(port, port + 20):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", candidate), Handler)
            server.daemon_threads = True
            return server
        except OSError as exc:
            last = exc
    raise OSError(f"no free port from {port} to {port + 19}: {last}")


def serve(report: Path, port: int = 4848, open_browser: bool = False) -> None:
    server = make_server(report, port)
    url = f"http://localhost:{server.server_address[1]}/"
    print(f"CodeAtlas report: {url}")
    print(f"Or open the file directly (no server needed): {report}")
    print("Press Ctrl+C to stop. The server listens on 127.0.0.1 and serves only this file.")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
