"""Self-contained frontend server + same-origin /api/* reverse proxy (D5/D6).

The previous-official-frontend and candidate-frontend journeys serve a static
frontend directory and forward ``/api/*`` requests to the staging ALB, giving
the frontend a same-origin API without modifying any files. The implementation
uses only the standard library (http.server + urllib), binds to 127.0.0.1 on a
random port, bounds every request, and refuses path traversal. No secrets are
involved.
"""

from __future__ import annotations

import json
import mimetypes
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .errors import ReadError, ValidationError

_REQUEST_TIMEOUT_SECONDS = 30


class _ProxyHandler(BaseHTTPRequestHandler):
    server_version = "onlineshop-delivery"

    def do_GET(self) -> None:
        if self.path == "/api" or self.path.startswith("/api/"):
            self._proxy()
            return
        self._serve_file()

    def _proxy(self) -> None:
        upstream = f"{self.server.upstream_url.rstrip('/')}{self.path}"
        request = urllib.request.Request(upstream, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS) as response:
                status = response.status
                content_type = response.headers.get(
                    "Content-Type", "application/octet-stream"
                )
                headers = {"Content-Type": content_type}
                body = response.read()
        except urllib.error.HTTPError as error:
            status = error.code
            headers = {"Content-Type": "application/json"}
            body = b'{"error":"upstream rejected the request"}'
        except (urllib.error.URLError, OSError) as error:
            status = 502
            headers = {"Content-Type": "application/json"}
            body = json.dumps({"error": f"upstream unreachable: {error}"}).encode()
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_file(self) -> None:
        relative = self.path.split("?", 1)[0].lstrip("/") or "index.html"
        candidate = (self.server.www_dir / relative).resolve()
        root = self.server.www_dir.resolve()
        if candidate != root and root not in candidate.parents:
            self.send_error(403, "forbidden")
            return
        if not candidate.is_file():
            self.send_error(404, "not found")
            return
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        body = candidate.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:
        return


class FrontendServer:
    """Context manager serving a static directory with an /api/* proxy."""

    def __init__(self, www_dir: str | Path, upstream_url: str):
        path = Path(www_dir)
        if not path.is_dir():
            raise ValidationError(f"frontend directory {path} is not a directory")
        if not upstream_url.startswith(("http://", "https://")):
            raise ValidationError(f"upstream URL must be http(s), got {upstream_url!r}")
        self.www_dir = path
        self.upstream_url = upstream_url
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def __enter__(self) -> FrontendServer:
        handler = type("Handler", (_ProxyHandler,), {})
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._server.www_dir = self.www_dir  # type: ignore[attr-defined]
        self._server.upstream_url = self.upstream_url  # type: ignore[attr-defined]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc_info) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=10)

    @property
    def base_url(self) -> str:
        if self._server is None:
            raise ReadError("server is not running")
        host, port = self._server.server_address
        return f"http://{host}:{port}"


@dataclass(frozen=True)
class JourneyResult:
    name: str
    conclusion: str
    detail: str = ""


def _fetch(url: str) -> tuple[int, dict, bytes]:
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read() or b""
    except (urllib.error.URLError, OSError) as error:
        raise ReadError(f"GET {url} failed: {error}") from error


def _journey_frontend_index(base_url: str) -> JourneyResult:
    status, headers, body = _fetch(f"{base_url}/")
    content_type = headers.get("Content-Type", headers.get("content-type", ""))
    detail = f"HTTP {status}, content-type {content_type}, {len(body)} bytes"
    if status != 200 or "text/html" not in content_type:
        return JourneyResult("frontend-index", "failed", detail)
    if 'id="root"' not in body.decode("utf-8", errors="replace"):
        return JourneyResult("frontend-index", "failed", f"{detail}; mount point missing")
    return JourneyResult("frontend-index", "passed", detail)


def _journey_items_api(base_url: str) -> JourneyResult:
    status, headers, body = _fetch(f"{base_url}/api/v1/items")
    content_type = headers.get("Content-Type", headers.get("content-type", ""))
    detail = f"HTTP {status}, content-type {content_type}, {len(body)} bytes"
    if status not in (200, 401, 403):
        return JourneyResult("items-api", "failed", detail)
    if "application/json" not in content_type:
        return JourneyResult("items-api", "failed", f"{detail}; not JSON")
    return JourneyResult("items-api", "passed", detail)


def _journey_health(upstream_url: str) -> JourneyResult:
    status, headers, _body = _fetch(f"{upstream_url.rstrip('/')}/actuator/health")
    content_type = headers.get("Content-Type", headers.get("content-type", ""))
    detail = f"HTTP {status}, content-type {content_type}"
    if status != 200:
        return JourneyResult("gateway-health", "failed", detail)
    return JourneyResult("gateway-health", "passed", detail)


def run_readonly_journeys(base_url: str, upstream_url: str) -> list[JourneyResult]:
    """Run the read-only journeys and return one conclusion per journey."""
    return [
        _journey_frontend_index(base_url),
        _journey_items_api(base_url),
        _journey_health(upstream_url),
    ]
