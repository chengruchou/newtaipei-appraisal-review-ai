"""The production transport does exactly one hop against a loopback server."""

import http.server
import threading

from appraisal_review.adapters.runtime_transport import system_resolve, urllib_fetch


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/ok":
            body = b'{"rows": []}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/hop":
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:1/never-followed")
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self.send_response(404)
            self.send_header("Content-Length", "9")
            self.end_headers()
            self.wfile.write(b"not found")

    def log_message(self, *args: object) -> None:
        return


def _serve() -> tuple[http.server.HTTPServer, str]:
    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


class TestUrllibFetch:
    def test_success_returns_body_and_headers(self) -> None:
        server, base = _serve()
        try:
            result = urllib_fetch()(base + "/ok")
        finally:
            server.shutdown()
        assert result.status_code == 200
        assert result.body == b'{"rows": []}'
        assert result.redirect_to is None
        assert "application/json" in result.headers.get("Content-Type", "")

    def test_redirect_is_reported_not_followed(self) -> None:
        server, base = _serve()
        try:
            result = urllib_fetch()(base + "/hop")
        finally:
            server.shutdown()
        assert result.status_code == 302
        assert result.redirect_to == "http://127.0.0.1:1/never-followed"

    def test_error_status_body_is_captured(self) -> None:
        server, base = _serve()
        try:
            result = urllib_fetch()(base + "/missing")
        finally:
            server.shutdown()
        assert result.status_code == 404
        assert result.body == b"not found"


class TestSystemResolve:
    def test_localhost_resolves_to_loopback(self) -> None:
        addresses = system_resolve()("localhost")
        assert any(address.startswith("127.") or address == "::1" for address in addresses)
