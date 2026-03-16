from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from jukebotx_api.main import get_web_session, get_web_session_audio
from jukebotx_core.ports.repositories import OpusJobCreate, QueueItem, Track, WebSession
from jukebotx_infra.opus_cache import OpusCacheService
from jukebotx_infra.storage import OpusStorageConfig, OpusStorageService


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _make_track(
    *,
    track_id: UUID | None = None,
    title: str = "Chaotic Peace",
    artist_display: str = "Villains Among Thieves",
    artist_username: str = "villains",
    lyrics: str | None = "I can hear the room breathe.",
    image_url: str | None = "https://cdn.example/artwork.jpg",
    video_url: str | None = "https://cdn.example/video.mp4",
    mp3_url: str | None = "https://cdn.example/track.mp3",
    web_audio_status: str | None = "completed",
) -> Track:
    timestamp = _now()
    resolved_id = track_id or uuid4()
    return Track(
        id=resolved_id,
        suno_url=f"https://suno.com/song/{resolved_id}",
        title=title,
        artist_display=artist_display,
        artist_username=artist_username,
        lyrics=lyrics,
        image_url=image_url,
        video_url=video_url,
        mp3_url=mp3_url,
        opus_url=None,
        opus_path=None,
        opus_status=None,
        opus_transcoded_at=None,
        created_at=timestamp,
        updated_at=timestamp,
        web_audio_url=f"https://media.example/{resolved_id}.ogg" if web_audio_status == "completed" else None,
        web_audio_path=f"web/{resolved_id}.ogg" if web_audio_status == "completed" else None,
        web_audio_status=web_audio_status,
        web_audio_transcoded_at=timestamp if web_audio_status == "completed" else None,
    )


def _make_queue_item(*, track_id: UUID, position: int) -> QueueItem:
    timestamp = _now()
    return QueueItem(
        id=uuid4(),
        guild_id=123,
        track_id=track_id,
        requested_by=456,
        status="queued",
        position=position,
        created_at=timestamp,
        updated_at=timestamp,
    )


def _make_session(
    *,
    session_id: UUID | None = None,
    current_track_id: UUID | None,
    is_active: bool = True,
) -> WebSession:
    timestamp = _now()
    return WebSession(
        id=uuid4(),
        session_id=session_id or uuid4(),
        guild_id=123,
        channel_id=789,
        current_track_id=current_track_id,
        activated_by=42,
        is_active=is_active,
        activated_at=timestamp if is_active else None,
        ended_at=None if is_active else timestamp,
        created_at=timestamp,
        updated_at=timestamp,
    )


class StubTrackRepo:
    def __init__(self, tracks: dict[UUID, Track]) -> None:
        self._tracks = tracks

    async def get_by_id(self, track_id: UUID) -> Track:
        try:
            return self._tracks[track_id]
        except KeyError as exc:
            raise KeyError(f"Track not found: {track_id}") from exc


class StubQueueRepo:
    def __init__(self, items: list[QueueItem]) -> None:
        self._items = items

    async def preview(self, *, guild_id: int, limit: int) -> list[QueueItem]:
        assert guild_id == 123
        return self._items[:limit]


class StubWebSessionRepo:
    def __init__(self, session: WebSession | None) -> None:
        self._session = session

    async def get_by_session_id(self, *, session_id: UUID) -> WebSession | None:
        if self._session is None or self._session.session_id != session_id:
            return None
        return self._session


class StubOpusJobs:
    def __init__(self) -> None:
        self.enqueued: list[OpusJobCreate] = []

    async def enqueue(self, data: OpusJobCreate) -> SimpleNamespace:
        self.enqueued.append(data)
        return SimpleNamespace(status="queued")


def _disabled_storage() -> OpusStorageService:
    return OpusStorageService(
        OpusStorageConfig(
            provider="",
            bucket="",
            prefix="",
            region="",
            endpoint_url="",
            access_key_id="",
            secret_access_key="",
            public_base_url="",
            signed_url_ttl_seconds=0,
            ttl_seconds=0,
        )
    )


@pytest.mark.asyncio
async def test_get_web_session_returns_listener_snapshot_with_queue_preview() -> None:
    current_track = _make_track(title="Now Playing", lyrics="Current lyrics")
    queued_track = _make_track(title="Up Next", lyrics="Next lyrics")
    session = _make_session(current_track_id=current_track.id)
    queue_items = [
        _make_queue_item(track_id=current_track.id, position=1),
        _make_queue_item(track_id=queued_track.id, position=2),
        _make_queue_item(track_id=uuid4(), position=3),
    ]

    response = await get_web_session(
        session_id=session.session_id,
        queue_limit=10,
        web_session_repo=StubWebSessionRepo(session),
        queue_repo=StubQueueRepo(queue_items),
        track_repo=StubTrackRepo(
            {
                current_track.id: current_track,
                queued_track.id: queued_track,
            }
        ),
    )

    assert response.status == "live"
    assert response.current_audio_url == f"/sessions/{session.session_id}/audio"
    assert response.current_track is not None
    assert response.current_track.track_id == current_track.id
    assert response.current_track.lyrics == "Current lyrics"
    assert [item.track_id for item in response.queue] == [queued_track.id]
    assert response.queue[0].title == "Up Next"


@pytest.mark.asyncio
async def test_get_web_session_returns_offline_state_when_inactive() -> None:
    session = _make_session(current_track_id=None, is_active=False)

    response = await get_web_session(
        session_id=session.session_id,
        queue_limit=10,
        web_session_repo=StubWebSessionRepo(session),
        queue_repo=StubQueueRepo([]),
        track_repo=StubTrackRepo({}),
    )

    assert response.status == "offline"
    assert response.current_track is None
    assert response.current_audio_url is None
    assert response.queue == []


@pytest.mark.asyncio
async def test_get_web_session_audio_uses_current_track_and_enqueues_web_audio_job(tmp_path) -> None:
    track = _make_track(web_audio_status="queued")
    session = _make_session(current_track_id=track.id)
    opus_jobs = StubOpusJobs()

    response = await get_web_session_audio(
        session_id=session.session_id,
        web_session_repo=StubWebSessionRepo(session),
        track_repo=StubTrackRepo({track.id: track}),
        opus_cache=OpusCacheService(cache_dir=tmp_path, ttl_seconds=300),
        opus_storage=_disabled_storage(),
        opus_jobs=opus_jobs,
    )

    assert response.status_code == 307
    assert response.headers["location"] == track.mp3_url
    assert [job.track_id for job in opus_jobs.enqueued] == [track.id]


@pytest.mark.asyncio
async def test_get_web_session_audio_rejects_inactive_session(tmp_path) -> None:
    session = _make_session(current_track_id=uuid4(), is_active=False)

    with pytest.raises(HTTPException) as excinfo:
        await get_web_session_audio(
            session_id=session.session_id,
            web_session_repo=StubWebSessionRepo(session),
            track_repo=StubTrackRepo({}),
            opus_cache=OpusCacheService(cache_dir=tmp_path, ttl_seconds=300),
            opus_storage=_disabled_storage(),
            opus_jobs=StubOpusJobs(),
        )

    assert excinfo.value.status_code == 409
