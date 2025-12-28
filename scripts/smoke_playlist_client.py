from __future__ import annotations

import asyncio
import sys

from jukebotx_infra.suno.playlist_client import HttpxSunoPlaylistClient


async def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: poetry run python scripts/smoke_playlist_client.py <playlist_url>")

    url = sys.argv[1]
    client = HttpxSunoPlaylistClient()
    data = await client.fetch_playlist(url)

    print(f"Playlist: {data.playlist_url}")
    print(f"Tracks found: {len(data.mp3_urls)}")
    for i, mp3 in enumerate(data.mp3_urls, start=1):
        print(f"{i:02d}. {mp3}")


if __name__ == "__main__":
    asyncio.run(main())
