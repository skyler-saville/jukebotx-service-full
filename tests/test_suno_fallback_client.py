from __future__ import annotations

import pytest

from jukebotx_core.ports.suno_client import SunoTrackData
from jukebotx_infra.suno.fallback_client import FallbackSunoClient


class _StubClient:
    def __init__(
        self, data: SunoTrackData | None = None, error: Exception | None = None
    ) -> None:
        self._data = data
        self._error = error
        self.calls = 0

    async def fetch_track(self, suno_url: str) -> SunoTrackData:
        self.calls += 1
        if self._error is not None:
            raise self._error
        assert self._data is not None
        return self._data


def _track(**kwargs) -> SunoTrackData:
    base = dict(
        suno_url="https://suno.com/song/abc",
        title="title",
        artist_display="artist",
        artist_username="artist_u",
        lyrics="lyrics",
        image_url="https://img",
        video_url="https://video",
        mp3_url="https://mp3",
    )
    base.update(kwargs)
    return SunoTrackData(**base)


@pytest.mark.asyncio
async def test_uses_primary_without_fallback_when_complete() -> None:
    primary = _StubClient(data=_track())
    fallback = _StubClient(data=_track(title="fallback"))

    client = FallbackSunoClient(primary_client=primary, fallback_client=fallback)
    result = await client.fetch_track("https://suno.com/song/abc")

    assert result.title == "title"
    assert primary.calls == 1
    assert fallback.calls == 0


@pytest.mark.asyncio
async def test_merges_missing_primary_fields_from_fallback() -> None:
    primary = _StubClient(data=_track(title=None, image_url=None, mp3_url=None))
    fallback = _StubClient(
        data=_track(
            title="fallback-title",
            image_url="https://fallback-img",
            mp3_url="https://fallback-mp3",
        )
    )

    client = FallbackSunoClient(primary_client=primary, fallback_client=fallback)
    result = await client.fetch_track("https://suno.com/song/abc")

    assert result.title == "fallback-title"
    assert result.image_url == "https://fallback-img"
    assert result.mp3_url == "https://fallback-mp3"
    assert result.artist_display == "artist"


@pytest.mark.asyncio
async def test_uses_fallback_on_primary_failure() -> None:
    primary = _StubClient(error=RuntimeError("boom"))
    fallback = _StubClient(data=_track(title="from-fallback"))

    client = FallbackSunoClient(primary_client=primary, fallback_client=fallback)
    result = await client.fetch_track("https://suno.com/song/abc")

    assert result.title == "from-fallback"
    assert primary.calls == 1
    assert fallback.calls == 1
