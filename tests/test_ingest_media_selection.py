from pathlib import Path
import sys
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT / "packages" / "core"))

from jukebotx_core.ports.repositories import (
    QueueItemCreate,
    SubmissionCreate,
    Track,
    TrackUpsert,
)
from jukebotx_core.ports.suno_client import SunoTrackData
from jukebotx_core.use_cases.ingest_suno_links import IngestSunoLink, IngestSunoLinkInput


class FakeSunoClient:
    def __init__(self, *, image_url: str | None, video_url: str | None) -> None:
        self._image_url = image_url
        self._video_url = video_url

    async def fetch_track(self, suno_url: str) -> SunoTrackData:
        return SunoTrackData(
            suno_url=suno_url,
            title="Test Track",
            artist_display="Test Artist",
            artist_username="test",
            lyrics=None,
            image_url=self._image_url,
            video_url=self._video_url,
            mp3_url="https://cdn.suno.ai/test.mp3",
        )


class FakeTrackRepo:
    def __init__(self) -> None:
        self.last_upsert: TrackUpsert | None = None

    async def upsert(self, data: TrackUpsert) -> Track:
        self.last_upsert = data
        return Track(
            id=uuid4(),
            suno_url=data.suno_url,
            title=data.title,
            artist_display=data.artist_display,
            artist_username=data.artist_username,
            lyrics=data.lyrics,
            image_url=data.image_url,
            video_url=data.video_url,
            mp3_url=data.mp3_url,
            opus_url=None,
            opus_path=None,
            opus_status=None,
            opus_transcoded_at=None,
            created_at=None,
            updated_at=None,
        )


class FakeSubmissionRepo:
    async def get_first_submission_for_track_in_guild(self, *, guild_id: int, track_id):
        return None

    async def create(self, data: SubmissionCreate):
        return data


class FakeQueueRepo:
    async def enqueue(self, data: QueueItemCreate):
        return data


class FakeMediaTransformer:
    def __init__(self, gif_url: str | None) -> None:
        self.gif_url = gif_url
        self.calls: list[str] = []

    async def mp4_to_gif(self, *, video_url: str) -> str | None:
        self.calls.append(video_url)
        return self.gif_url


@pytest.mark.parametrize(
    ("image_url", "video_url", "expected_media_url"),
    [
        ("https://cdn.suno.ai/cover.jpg", "https://cdn.suno.ai/video.mp4", "https://cdn.suno.ai/cover.jpg"),
        (None, "https://cdn.suno.ai/video.mp4", "https://cdn.suno.ai/video.mp4"),
    ],
)
def test_ingest_media_url_prefers_image_then_falls_back_to_video(
    image_url: str | None,
    video_url: str | None,
    expected_media_url: str,
) -> None:
    ingest = IngestSunoLink(
        suno_client=FakeSunoClient(image_url=image_url, video_url=video_url),
        track_repo=FakeTrackRepo(),
        submission_repo=FakeSubmissionRepo(),
        queue_repo=FakeQueueRepo(),
    )

    import asyncio

    result = asyncio.run(ingest.execute(
        IngestSunoLinkInput(
            guild_id=123,
            channel_id=456,
            message_id=789,
            author_id=111,
            suno_url="https://suno.com/song/abc123",
        )
    ))

    assert result.media_url == expected_media_url


def test_ingest_converts_mp4_to_gif_when_image_missing() -> None:
    transformer = FakeMediaTransformer("https://minio.example.com/media/video.gif")
    track_repo = FakeTrackRepo()
    ingest = IngestSunoLink(
        suno_client=FakeSunoClient(image_url=None, video_url="https://cdn.suno.ai/video.mp4"),
        track_repo=track_repo,
        submission_repo=FakeSubmissionRepo(),
        queue_repo=FakeQueueRepo(),
        media_transformer=transformer,
    )

    import asyncio

    result = asyncio.run(ingest.execute(
        IngestSunoLinkInput(
            guild_id=123,
            channel_id=456,
            message_id=789,
            author_id=111,
            suno_url="https://suno.com/song/abc123",
        )
    ))

    assert transformer.calls == ["https://cdn.suno.ai/video.mp4"]
    assert track_repo.last_upsert is not None
    assert track_repo.last_upsert.image_url == "https://minio.example.com/media/video.gif"
    assert track_repo.last_upsert.video_url == "https://cdn.suno.ai/video.mp4"
    assert result.media_url == "https://minio.example.com/media/video.gif"
