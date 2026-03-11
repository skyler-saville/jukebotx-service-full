from __future__ import annotations

import json
import os
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"


class WebHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, directory: str | None = None, **kwargs) -> None:
        super().__init__(*args, directory=directory or str(STATIC_DIR), **kwargs)

    def do_GET(self) -> None:  # noqa: N802 - stdlib interface
        if self.path == "/config.json":
            self._serve_config()
            return
        if self.path.startswith("/api/"):
            self._proxy_request()
            return
        super().do_GET()

    def _serve_config(self) -> None:
        payload = {
            "apiBasePath": "/api",
            "publicApiBaseUrl": os.getenv("PUBLIC_API_BASE_URL", "").rstrip("/"),
            "publicFrontendUrl": os.getenv("PUBLIC_FRONTEND_URL", "").rstrip("/"),
            "environment": os.getenv("ENV", "development"),
        }
        body = json.dumps(payload).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _proxy_request(self) -> None:
        upstream = os.getenv("WEB_API_UPSTREAM", "http://api:8000").rstrip("/")
        target_url = f"{upstream}{self.path[4:]}"
        request = Request(target_url, headers=self._upstream_headers())
        try:
            with urlopen(request, timeout=15) as response:
                body = response.read()
                self.send_response(response.status)
                for key, value in response.headers.items():
                    if key.lower() in {"content-length", "connection", "transfer-encoding"}:
                        continue
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        except HTTPError as exc:
            body = exc.read()
            self.send_response(exc.code)
            for key, value in exc.headers.items():
                if key.lower() in {"content-length", "connection", "transfer-encoding"}:
                    continue
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except URLError as exc:
            body = json.dumps({"detail": f"API upstream unavailable: {exc.reason}"}).encode("utf-8")
            self.send_response(HTTPStatus.BAD_GATEWAY)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def _upstream_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if "Cookie" in self.headers:
            headers["Cookie"] = self.headers["Cookie"]
        if "Authorization" in self.headers:
            headers["Authorization"] = self.headers["Authorization"]
        return headers


def main() -> None:
    host = os.getenv("WEB_HOST", "0.0.0.0")
    port = int(os.getenv("WEB_PORT", "3000"))
    handler = partial(WebHandler, directory=str(STATIC_DIR))
    server = ThreadingHTTPServer((host, port), handler)
    public_frontend_url = os.getenv("PUBLIC_FRONTEND_URL", "").rstrip("/")
    if public_frontend_url:
        print(f"Web UI listening on http://{host}:{port} (public: {public_frontend_url})")
    else:
        print(f"Web UI listening on http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
