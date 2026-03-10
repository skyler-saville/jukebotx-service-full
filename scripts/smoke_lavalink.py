from __future__ import annotations

import argparse
import asyncio
import json
import socket
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from http.client import HTTPResponse


def _request(url: str, headers: dict[str, str]) -> tuple[int, str]:
    req = Request(url, headers=headers)
    try:
        with urlopen(req, timeout=15) as resp:
            body = resp.read().decode(errors="replace")
            return resp.getcode(), body
    except HTTPError as exc:
        body = exc.read().decode(errors="replace")
        return exc.code, body
    except URLError as exc:
        raise RuntimeError(f"Request failed for {url}: {exc}") from exc


def _websocket_upgrade_status(host: str, port: int, headers: dict[str, str]) -> int:
    key = "dGhlIHNhbXBsZSBub25jZQ=="
    lines = [
        "GET /v4/websocket HTTP/1.1",
        f"Host: {host}:{port}",
        "Upgrade: websocket",
        "Connection: Upgrade",
        f"Sec-WebSocket-Key: {key}",
        "Sec-WebSocket-Version: 13",
    ]
    for name, value in headers.items():
        lines.append(f"{name}: {value}")
    lines.append("")
    lines.append("")
    payload = "\r\n".join(lines).encode()

    with socket.create_connection((host, port), timeout=10) as sock:
        sock.sendall(payload)
        response = sock.recv(4096).decode(errors="replace")

    first_line = response.splitlines()[0] if response else ""
    try:
        return int(first_line.split()[1])
    except Exception as exc:  # pragma: no cover - defensive parse fallback
        raise RuntimeError(f"Unexpected websocket response: {first_line!r}") from exc


async def main() -> None:
    parser = argparse.ArgumentParser(description="Lavalink smoke test")
    parser.add_argument("--host", default="lavalink")
    parser.add_argument("--port", type=int, default=2333)
    parser.add_argument("--password", required=True)
    parser.add_argument("--client-name", default="jukebotx/smoke")
    parser.add_argument("--user-id", default="1")
    parser.add_argument("--identifier", default=None)
    args = parser.parse_args()

    base_url = f"http://{args.host}:{args.port}"
    headers = {
        "Authorization": args.password,
        "Client-Name": args.client_name,
        "User-Id": str(args.user_id),
    }

    version_status, version_body = _request(f"{base_url}/version", headers)
    print("version status:", version_status)
    print("version body:", version_body.strip())
    if version_status >= 400:
        raise SystemExit("Version request failed.")

    ws_status = _websocket_upgrade_status(args.host, args.port, headers)
    print("websocket upgrade status:", ws_status)
    if ws_status != 101:
        raise SystemExit("WebSocket upgrade failed.")

    if args.identifier:
        query = urlencode({"identifier": args.identifier})
        load_status, load_body = _request(f"{base_url}/v4/loadtracks?{query}", headers)
        print("loadtracks status:", load_status)
        if load_status >= 400:
            print("loadtracks body:", load_body[:500])
            raise SystemExit("loadtracks request failed.")
        payload = json.loads(load_body)
        load_type = payload.get("loadType")
        print("loadType:", load_type)
        if load_type == "error":
            print("loadtracks error payload:", json.dumps(payload.get("data"), indent=2))
            raise SystemExit("loadtracks returned loadType=error.")
        data = payload.get("data")
        if isinstance(data, dict):
            info = data.get("info", {})
            print("track title:", info.get("title"))
            print("track author:", info.get("author"))
        else:
            print("loadtracks data:", json.dumps(data)[:300])

    print("Lavalink smoke: OK")


if __name__ == "__main__":
    asyncio.run(main())
