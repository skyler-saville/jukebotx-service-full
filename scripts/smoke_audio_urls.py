# scripts/smoke_audio_urls.py
from __future__ import annotations

import asyncio
import os
import subprocess

from jukebotx_infra.suno.client import HttpxSunoClient
from jukebotx_infra.suno.playlist_client import HttpxSunoPlaylistClient


def _run_ffmpeg(url: str, label: str) -> None:
    print(f"Running ffmpeg for {label}: {url}")
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            url,
            "-t",
            "1",
            "-f",
            "null",
            "-",
        ],
        check=True,
    )


async def main() -> None:
    song_url = os.environ.get("SUNO_SMOKE_URL")
    playlist_url = os.environ.get("PLAYLIST_SMOKE_URL")

    if not song_url or not playlist_url:
        raise SystemExit("SUNO_SMOKE_URL and PLAYLIST_SMOKE_URL must be set")

    suno_client = HttpxSunoClient()
    playlist_client = HttpxSunoPlaylistClient()

    song_data = await suno_client.fetch_track(song_url)
    if not song_data.mp3_url:
        raise SystemExit(f"No mp3_url found for SUNO_SMOKE_URL={song_url}")

    print("Song page URL:", song_data.suno_url)
    print("Song mp3 URL:", song_data.mp3_url)
    _run_ffmpeg(song_data.mp3_url, "song")

    playlist_data = await playlist_client.fetch_playlist(playlist_url)
    if not playlist_data.mp3_urls:
        raise SystemExit(f"No mp3 URLs found for PLAYLIST_SMOKE_URL={playlist_url}")

    print("Playlist URL:", playlist_url)
    print("Playlist mp3 count:", len(playlist_data.mp3_urls))

    for idx, mp3_url in enumerate(playlist_data.mp3_urls[:3], start=1):
        _run_ffmpeg(mp3_url, f"playlist[{idx}]")

    if len(playlist_data.mp3_urls) > 3:
        print("Skipping remaining playlist tracks for brevity.")


if __name__ == "__main__":
    asyncio.run(main())
