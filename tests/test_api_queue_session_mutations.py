import asyncio
from datetime import datetime, timezone
from pathlib import Path
import sys
from uuid import UUID, uuid4
from contextlib import asynccontextmanager
from types import ModuleType

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
sys.path.extend(
    [
        str(ROOT / "apps" / "api"),
        str(ROOT / "packages" / "core"),
        str(ROOT / "packages" / "infra"),
    ]
)

if "async_timeout" not in sys.modules:
    mod = ModuleType("async_timeout")

    @asynccontextmanager
    async def timeout(*args, **kwargs):
        yield

    mod.timeout = timeout
    sys.modules["async_timeout"] = mod


from jukebotx_api.auth import SessionData
from jukebotx_api.main import (
    clear_queue_items,
    enqueue_queue_item,
    mark_queue_item_played,
    remove_queue_item,
    set_session_autoplay,
    set_session_cooldown,
    set_session_dj,
    set_session_open,
    set_session_track_limit,
    skip_queue_item,
)
from jukebotx_api.schemas import (
    EnqueueTrackRequest,
    SessionAutoplayRequest,
    SessionCooldownRequest,
    SessionDjRequest,
    SessionOpenRequest,
    SessionTrackLimitRequest,
)
from jukebotx_core.ports.repositories import Track
from jukebotx_infra.repos.guild_config_repo import InMemoryGuildConfigRepository
from jukebotx_infra.repos.memory import InMemoryQueueRepository


class FakeTrackRepo:
    def __init__(self, track: Track) -> None:
        self._track = track

    async def get_by_id(self, track_id: UUID) -> Track:
        if track_id != self._track.id:
            raise KeyError(f"Track not found: {track_id}")
        return self._track


def _session(*, guild_ids: list[str] | None = None) -> SessionData:
    return SessionData(
        user_id="42",
        username="alice",
        discriminator="0",
        avatar=None,
        guild_ids=guild_ids or ["123"],
        issued_at=datetime.now(timezone.utc),
    )


def _track() -> Track:
    now = datetime.now(timezone.utc)
    return Track(
        id=uuid4(),
        suno_url="https://suno.com/song/abc",
        title="Song",
        artist_display="artist",
        artist_username="artist",
        lyrics=None,
        image_url=None,
        video_url=None,
        mp3_url="https://audio.mp3",
        opus_url=None,
        opus_path=None,
        opus_status=None,
        opus_transcoded_at=None,
        created_at=now,
        updated_at=now,
    )


def test_enqueue_skip_clear_remove_and_mark_played() -> None:
    queue_repo = InMemoryQueueRepository()
    track = _track()
    session = _session()

    queued = asyncio.run(enqueue_queue_item(
        guild_id=123,
        payload=EnqueueTrackRequest(track_id=track.id),
        session=session,
        queue_repo=queue_repo,
        track_repo=FakeTrackRepo(track),
    ))
    assert queued.status == "queued"
    assert queued.requested_by == 42

    skipped = asyncio.run(skip_queue_item(guild_id=123, queue_item_id=queued.id, session=session, queue_repo=queue_repo))
    assert skipped.ok is True

    # enqueue a second item to test played/remove/clear actions
    queued2 = asyncio.run(enqueue_queue_item(
        guild_id=123,
        payload=EnqueueTrackRequest(track_id=track.id),
        session=session,
        queue_repo=queue_repo,
        track_repo=FakeTrackRepo(track),
    ))
    played = asyncio.run(mark_queue_item_played(guild_id=123, queue_item_id=queued2.id, session=session, queue_repo=queue_repo))
    assert played.ok is True

    queued3 = asyncio.run(enqueue_queue_item(
        guild_id=123,
        payload=EnqueueTrackRequest(track_id=track.id),
        session=session,
        queue_repo=queue_repo,
        track_repo=FakeTrackRepo(track),
    ))
    removed = asyncio.run(remove_queue_item(guild_id=123, queue_item_id=queued3.id, session=session, queue_repo=queue_repo))
    assert removed.ok is True

    cleared = asyncio.run(clear_queue_items(guild_id=123, session=session, queue_repo=queue_repo))
    assert cleared.ok is True


def test_mutation_endpoints_reject_unauthorized_guild() -> None:
    queue_repo = InMemoryQueueRepository()
    track = _track()
    session = _session(guild_ids=["999"])

    with pytest.raises(HTTPException, match="Forbidden"):
        asyncio.run(enqueue_queue_item(
            guild_id=123,
            payload=EnqueueTrackRequest(track_id=track.id),
            session=session,
            queue_repo=queue_repo,
            track_repo=FakeTrackRepo(track),
        ))


def test_session_mode_controls_update_guild_config() -> None:
    repo = InMemoryGuildConfigRepository()
    session = _session()

    opened = asyncio.run(set_session_open(
        guild_id=123,
        payload=SessionOpenRequest(is_open=False),
        session=session,
        guild_config_repo=repo,
    ))
    assert opened.session_open is False

    limited = asyncio.run(set_session_track_limit(
        guild_id=123,
        payload=SessionTrackLimitRequest(track_limit=3),
        session=session,
        guild_config_repo=repo,
    ))
    assert limited.session_track_limit == 3

    autoplay = asyncio.run(set_session_autoplay(
        guild_id=123,
        payload=SessionAutoplayRequest(enabled=True, remaining=5),
        session=session,
        guild_config_repo=repo,
    ))
    assert autoplay.autoplay_enabled is True
    assert autoplay.autoplay_remaining == 5

    dj = asyncio.run(set_session_dj(
        guild_id=123,
        payload=SessionDjRequest(enabled=True, remaining=2),
        session=session,
        guild_config_repo=repo,
    ))
    assert dj.dj_enabled is True
    assert dj.dj_remaining == 2

    cooldown = asyncio.run(set_session_cooldown(
        guild_id=123,
        payload=SessionCooldownRequest(mode="queue", seconds=120),
        session=session,
        guild_config_repo=repo,
    ))
    assert cooldown.cooldown_mode == "queue"
    assert cooldown.submission_cooldown_seconds == 120
