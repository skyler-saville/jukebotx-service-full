from pathlib import Path
import sys

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

ROOT = Path(__file__).resolve().parents[1]
sys.path.extend(
    [
        str(ROOT / "apps" / "bot"),
        str(ROOT / "packages" / "core"),
        str(ROOT / "packages" / "infra"),
    ]
)

from jukebotx_bot.discord.suno import extract_suno_urls
from jukebotx_bot.main import select_playback_url
from jukebotx_core.ports.gif_generation_queue import GifCleanupItem, GifGenerationQueue, GifGenerationRequest
from jukebotx_core.ports.suno_client import SunoTrackData
from jukebotx_core.use_cases.ingest_suno_links import IngestSunoLink, IngestSunoLinkInput
from jukebotx_infra.db.models import Base
from jukebotx_infra.db.session import DATABASE_URL
from jukebotx_infra.repos.queue_repo import PostgresQueueRepository
from jukebotx_infra.repos.submission_repo import PostgresSubmissionRepository
from jukebotx_infra.repos.track_repo import PostgresTrackRepository


@pytest.fixture(scope="session")
async def async_session_factory() -> async_sessionmaker:
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture(autouse=True)
async def cleanup_db(async_session_factory: async_sessionmaker) -> None:
    yield
    async with async_session_factory() as session:
        await session.execute(
            text("TRUNCATE TABLE queue_items, submissions, tracks RESTART IDENTITY CASCADE")
        )
        await session.commit()


class FakeSunoClient:
    async def fetch_track(self, suno_url: str) -> SunoTrackData:
        return SunoTrackData(
            suno_url=suno_url,
            title="Test Track",
            artist_display="Test Artist",
            artist_username="test",
            lyrics=None,
            image_url=None,
            video_url=None,
            mp3_url="https://cdn.suno.ai/test.mp3",
        )


class FakeGifQueue(GifGenerationQueue):
    def __init__(self) -> None:
        self.enqueued: list[GifGenerationRequest] = []

    async def enqueue(self, request: GifGenerationRequest) -> None:
        self.enqueued.append(request)

    async def delete_generated_gifs(self, items: list[GifCleanupItem]) -> None:
        return None


def test_extract_suno_urls() -> None:
    content = "Check this https://suno.com/song/abc123. And https://app.suno.ai/song/def456"
    assert extract_suno_urls(content) == [
        "https://suno.com/song/abc123",
        "https://app.suno.ai/song/def456",
    ]


def test_select_playback_url_prefers_mp3() -> None:
    assert (
        select_playback_url(
            suno_url="https://suno.com/song/c3f5745b-2899-4fcf-b482-4dbe5e49931b",
            mp3_url="https://cdn1.suno.ai/c3f5745b-2899-4fcf-b482-4dbe5e49931b.mp3",
        )
        == "https://cdn1.suno.ai/c3f5745b-2899-4fcf-b482-4dbe5e49931b.mp3"
    )


def test_select_playback_url_falls_back_to_suno_url() -> None:
    assert (
        select_playback_url(
            suno_url="https://suno.com/song/c3f5745b-2899-4fcf-b482-4dbe5e49931b",
            mp3_url=None,
        )
        == "https://suno.com/song/c3f5745b-2899-4fcf-b482-4dbe5e49931b"
    )


@pytest.mark.asyncio
async def test_ingest_suno_link_detects_duplicates_per_guild(
    async_session_factory: async_sessionmaker,
) -> None:
    ingest = IngestSunoLink(
        suno_client=FakeSunoClient(),
        track_repo=PostgresTrackRepository(async_session_factory),
        submission_repo=PostgresSubmissionRepository(async_session_factory),
        queue_repo=PostgresQueueRepository(async_session_factory),
        gif_queue=FakeGifQueue(),
    )

    input_data = IngestSunoLinkInput(
        guild_id=123,
        channel_id=456,
        message_id=789,
        author_id=111,
        suno_url="https://suno.com/song/abc123",
    )

    first = await ingest.execute(input_data)
    assert first.is_duplicate_in_guild is False

    second = await ingest.execute(input_data)
    assert second.is_duplicate_in_guild is True

    third = await ingest.execute(
        IngestSunoLinkInput(
            guild_id=999,
            channel_id=456,
            message_id=790,
            author_id=111,
            suno_url="https://suno.com/song/abc123",
        )
    )
    assert third.is_duplicate_in_guild is False
