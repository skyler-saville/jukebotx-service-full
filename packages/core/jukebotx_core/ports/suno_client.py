# packages/core/jukebotx_core/ports/suno_client.py
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SunoTrackData:
    """
    Data returned by a Suno client implementation.
    """
    suno_url: str
    title: str | None
    artist_display: str | None
    artist_username: str | None
    lyrics: str | None
    image_url: str | None
    video_url: str | None
    mp3_url: str | None

    @property
    def media_url(self) -> str | None:
        return self.video_url or self.image_url


class SunoClient:
    """
    Port interface: core depends on this, infra implements it.
    """

    async def fetch_track(self, suno_url: str) -> SunoTrackData:
        raise NotImplementedError
