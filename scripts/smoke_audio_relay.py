from __future__ import annotations

import argparse

import httpx


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify the audio relay API with its synthetic FFmpeg tone source."
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:18090")
    parser.add_argument("--token", default="")
    parser.add_argument("--duration", type=float, default=1.0)
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument(
        "--source-url",
        default="",
        help="Use a real browser source instead of the synthetic tone.",
    )
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    headers = {"Authorization": f"Bearer {args.token}"} if args.token else {}
    with httpx.Client(timeout=args.timeout) as client:
        source_url = args.source_url or (
            f"synthetic://tone?frequency=440&duration={args.duration}"
        )
        created = client.post(
            f"{base_url}/v1/streams",
            headers=headers,
            json={
                "source_url": source_url,
                "consumer_id": "smoke-test",
            },
        )
        created.raise_for_status()
        payload = created.json()
        stream_id = payload["id"]
        stream_url = payload["stream_url"]

        try:
            # The advertised URL targets the Docker network. Use the host base URL
            # for this operator-side smoke while preserving the returned path.
            stream_path = httpx.URL(stream_url).raw_path.decode()
            audio = bytearray()
            with client.stream("GET", f"{base_url}{stream_path}") as streamed:
                streamed.raise_for_status()
                for chunk in streamed.iter_bytes():
                    audio.extend(chunk)
                    if len(audio) >= 64 * 1024:
                        break
            if len(audio) < 64 or not audio.startswith(b"OggS"):
                raise RuntimeError("Relay did not return a plausible Ogg stream")
        finally:
            released = client.delete(
                f"{base_url}/v1/streams/{stream_id}",
                headers=headers,
            )
            if released.status_code not in {204, 404, 410}:
                released.raise_for_status()

    print(f"Relay smoke passed: {len(audio)} Ogg bytes from {source_url}")


if __name__ == "__main__":
    main()
