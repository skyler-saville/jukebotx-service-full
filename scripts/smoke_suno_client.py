# scripts/smoke_suno_client.py
from __future__ import annotations

import asyncio
import sys

from jukebotx_infra.suno.client import HttpxSunoClient


def _clip(text: str, *, head: int = 400, tail: int = 200) -> tuple[str, str]:
    """
    Return (head_preview, tail_preview) for a text block.

    Args:
        text: Full text.
        head: Number of chars from the start.
        tail: Number of chars from the end.

    Returns:
        Tuple of (head_preview, tail_preview).
    """
    return text[:head], text[-tail:]


async def main() -> None:
    """
    Smoke test for the Suno HTTP client.

    Usage:
        PYTHONPATH=apps/bot:apps/api:packages/core:packages/infra \
        poetry run python scripts/smoke_suno_client.py "https://suno.com/s/..."
    """
    if len(sys.argv) != 2:
        raise SystemExit("Usage: poetry run python scripts/smoke_suno_client.py <suno_url>")

    url = sys.argv[1]
    client = HttpxSunoClient()
    data = await client.fetch_track(url)

    print("URL:", data.suno_url)
    print("Title:", data.title)
    print("Artist:", data.artist_display)
    print("Artist Username:", data.artist_username)
    print("MP3:", data.mp3_url)
    print("Video:", data.video_url)
    print("Image:", data.image_url)
    print("Media (preferred):", data.media_url)

    print("Lyrics present?:", data.lyrics is not None)
    if data.lyrics is None:
        print("Lyrics length:", 0)
        print("Lyrics head preview:", None)
        print("Lyrics tail preview:", None)
        return

    head_preview, tail_preview = _clip(data.lyrics, head=400, tail=200)

    print("Lyrics length:", len(data.lyrics))

    print("Lyrics head preview:")
    print(head_preview + ("…" if len(data.lyrics) > 400 else ""))

    print("Lyrics tail preview:")
    print(tail_preview)

    # Optional: fail fast if the extraction looks suspiciously tiny
    # (helps catch regressions when Suno changes their markup).
    if len(data.lyrics) < 200:
        raise SystemExit("Suspiciously short lyrics extracted (<200 chars). Treat as failure.")


if __name__ == "__main__":
    asyncio.run(main())
