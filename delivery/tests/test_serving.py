"""Tests for the self-contained frontend serve+proxy module (D5/D6)."""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from delivery.errors import ReadError, ValidationError
from delivery.serving import FrontendServer, run_readonly_journeys


class _FakeUpstream(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/actuator/health":
            body = b'{"status":"UP"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/items":
            body = b'{"items": []}'
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)

    def log_message(self, format, *args):
        return


@pytest.fixture
def upstream():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeUpstream)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    yield f"http://{host}:{port}"
    server.shutdown()
    thread.join(timeout=5)


@pytest.fixture
def www_dir(tmp_path):
    (tmp_path / "index.html").write_text('<html><body><div id="root"></div></body></html>')
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "app.js").write_text("console.log('ok')")
    return tmp_path


def test_frontend_server_serves_index_and_proxies_api(www_dir, upstream):
    with FrontendServer(www_dir, upstream) as server:
        results = run_readonly_journeys(server.base_url, upstream)
    by_name = {result.name: result for result in results}
    assert by_name["frontend-index"].conclusion == "passed"
    assert by_name["items-api"].conclusion == "passed"
    assert "401" in by_name["items-api"].detail
    assert by_name["gateway-health"].conclusion == "passed"


def test_frontend_server_static_asset(www_dir, upstream):
    import urllib.request

    with (
        FrontendServer(www_dir, upstream) as server,
        urllib.request.urlopen(f"{server.base_url}/assets/app.js", timeout=10) as response,
    ):
        assert response.status == 200
        assert b"console.log" in response.read()


def test_frontend_server_blocks_path_traversal(www_dir, upstream, tmp_path):
    (tmp_path / "secret.txt").write_text("top secret")
    import urllib.error
    import urllib.request

    with FrontendServer(www_dir, upstream) as server:
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(f"{server.base_url}/../secret.txt", timeout=10)
        assert error.value.code == 403


def test_frontend_server_upstream_unreachable_returns_502(www_dir):
    with FrontendServer(www_dir, "http://127.0.0.1:1") as server, pytest.raises(ReadError):
        # the direct health journey fails closed on connection errors
        run_readonly_journeys(server.base_url, "http://127.0.0.1:1")


def test_frontend_server_upstream_error_is_journey_conclusion(www_dir):
    class _Failing(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_error(502)

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Failing)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    upstream = f"http://{host}:{port}"
    try:
        with FrontendServer(www_dir, upstream) as frontend:
            results = run_readonly_journeys(frontend.base_url, upstream)
        by_name = {result.name: result for result in results}
        assert by_name["items-api"].conclusion == "failed"
        assert "502" in by_name["items-api"].detail
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_frontend_server_rejects_missing_directory(tmp_path):
    with pytest.raises(ValidationError):
        FrontendServer(tmp_path / "does-not-exist", "http://x")


def test_frontend_server_rejects_bad_upstream(tmp_path):
    with pytest.raises(ValidationError):
        FrontendServer(tmp_path, "not-a-url")


def test_journey_index_requires_root_mount(www_dir, upstream):
    (www_dir / "index.html").write_text("<html><body>no mount</body></html>")
    with FrontendServer(www_dir, upstream) as server:
        results = run_readonly_journeys(server.base_url, upstream)
    by_name = {result.name: result for result in results}
    assert by_name["frontend-index"].conclusion == "failed"
