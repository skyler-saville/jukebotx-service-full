import asyncio
from datetime import datetime, timezone
from pathlib import Path
import sys
from contextlib import asynccontextmanager
from types import ModuleType, SimpleNamespace
from uuid import uuid4

from fastapi.responses import RedirectResponse

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

from jukebotx_core.ports.repositories import OpusJobCreate, Track
from jukebotx_api.main import get_track_opus, get_track_opus_status


class FakeTrackRepo:
    def __init__(self, track: Track) -> None:
        self._track = track

    async def get_by_id(self, track_id):
        assert track_id == self._track.id
        return self._track


class FakeOpusJobs:
    def __init__(self, queued_status: str = "queued") -> None:
        self.calls: list[OpusJobCreate] = []
        self._queued_status = queued_status

    async def enqueue(self, data: OpusJobCreate):
        self.calls.append(data)
        return SimpleNamespace(status=self._queued_status)


class FakeStorage:
    def __init__(self, *, enabled: bool, fresh: bool = False) -> None:
        self.is_enabled = enabled
        self._fresh = fresh

    def is_fresh(self, *, object_key: str) -> bool:
        assert object_key
        return self._fresh

    def get_access_url(self, *, object_key: str) -> str:
        return f"https://cdn.example/{object_key}"


class FakeCache:
    def cache_path(self, *, track_id):
        return Path(f"/tmp/{track_id}.opus")


def _track(*, opus_status: str | None, mp3_url: str = "https://audio.mp3", opus_url: str | None = None):
    now = datetime.now(timezone.utc)
    return Track(
        id=uuid4(),
        suno_url="https://suno.com/song/abc",
        title="Song",
        artist_display=None,
        artist_username=None,
        lyrics=None,
        image_url=None,
        video_url=None,
        mp3_url=mp3_url,
        opus_url=opus_url,
        opus_path="track.opus",
        opus_status=opus_status,
        opus_transcoded_at=None,
        created_at=now,
        updated_at=now,
    )


def test_get_track_opus_redirects_to_cached_storage_asset() -> None:
    track = _track(opus_status="completed")

    result = asyncio.run(
        get_track_opus(
            track_id=track.id,
            track_repo=FakeTrackRepo(track),
            opus_cache=FakeCache(),
            opus_storage=FakeStorage(enabled=True, fresh=True),
            opus_jobs=FakeOpusJobs(),
        )
    )

    assert isinstance(result, RedirectResponse)
    assert result.headers["location"] == "https://cdn.example/track.opus"


def test_get_track_opus_falls_back_to_mp3_and_enqueues_when_not_ready() -> None:
    track = _track(opus_status="queued", mp3_url="https://cdn.example/track.mp3")
    jobs = FakeOpusJobs()

    result = asyncio.run(
        get_track_opus(
            track_id=track.id,
            track_repo=FakeTrackRepo(track),
            opus_cache=FakeCache(),
            opus_storage=FakeStorage(enabled=True, fresh=False),
            opus_jobs=jobs,
        )
    )

    assert isinstance(result, RedirectResponse)
    assert result.headers["location"] == track.mp3_url
    assert len(jobs.calls) == 1
    assert jobs.calls[0].track_id == track.id


def test_get_track_opus_status_returns_failed_without_enqueuing() -> None:
    track = _track(opus_status="failed")
    jobs = FakeOpusJobs()

    result = asyncio.run(
        get_track_opus_status(
            track_id=track.id,
            track_repo=FakeTrackRepo(track),
            opus_cache=FakeCache(),
            opus_storage=FakeStorage(enabled=False),
            opus_jobs=jobs,
        )
    )

    assert result.ready is False
    assert result.status == "failed"
    assert jobs.calls == []


def test_get_track_opus_status_returns_queued_from_enqueue() -> None:
    track = _track(opus_status="queued")
    jobs = FakeOpusJobs(queued_status="queued")

    result = asyncio.run(
        get_track_opus_status(
            track_id=track.id,
            track_repo=FakeTrackRepo(track),
            opus_cache=FakeCache(),
            opus_storage=FakeStorage(enabled=False),
            opus_jobs=jobs,
        )
    )

    assert result.ready is False
    assert result.status == "queued"
    assert len(jobs.calls) == 1
