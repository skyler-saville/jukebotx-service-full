from __future__ import annotations

import asyncio
import sys

from jukebotx_infra.suno.playlist_client import HttpxSunoPlaylistClient


async def main() -> None:
    """
    Smoke test for Suno playlist scraping.

    Prints:
      - playlist URL
      - total track count
      - per-item:
          - source index
          - mp3 URL
          - suno track URL (if discovered)
    """
    if len(sys.argv) != 2:
        raise SystemExit(
            "Usage: poetry run python scripts/smoke_playlist_client.py <playlist_url>"
        )

    playlist_url = sys.argv[1].strip()

    client = HttpxSunoPlaylistClient()
    data = await client.fetch_playlist(playlist_url)

    print(f"Playlist: {data.playlist_url}")
    print(f"Tracks found: {len(data.items)}")
    print()

    missing_track_urls = 0

    for item in data.items:
        print(f"{item.source_index:02d}. MP3:")
        print(f"    {item.mp3_url}")

        if item.suno_track_url:
            print("    Track URL:")
            print(f"    {item.suno_track_url}")
        else:
            print("    Track URL:")
            print("    (not discovered)")
            missing_track_urls += 1

        print()

    if missing_track_urls == 0:
        print("✓ All tracks have associated Suno track URLs.")
    else:
        print(
            f"⚠ {missing_track_urls} / {len(data.items)} tracks are missing Suno track URLs."
        )
        print("  Metadata enrichment will rely on MP3-only fallback for those tracks.")

    print("\nNOTE:")
    print(
        "- MP3 URLs are guaranteed playback keys.\n"
        "- Track URLs are best-effort and may be unavailable on some playlists.\n"
        "- Pairing is conservative to avoid corrupt metadata."
    )


if __name__ == "__main__":
    asyncio.run(main())
