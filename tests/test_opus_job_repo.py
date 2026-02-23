import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
import os
import sys
from types import ModuleType
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

ROOT = Path(__file__).resolve().parents[1]
sys.path.extend([str(ROOT / "packages" / "core"), str(ROOT / "packages" / "infra")])

if "async_timeout" not in sys.modules:
    mod = ModuleType("async_timeout")

    @asynccontextmanager
    async def timeout(*args, **kwargs):
        yield

    mod.timeout = timeout
    sys.modules["async_timeout"] = mod

from jukebotx_core.ports.repositories import OpusJobCreate
from jukebotx_infra.db.models import Base, OpusJobModel
from jukebotx_infra.repos.opus_job_repo import PostgresOpusJobRepository

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://jukebotx:jukebotx@localhost:5432/jukebotx",
)


def _build_session_factory() -> async_sessionmaker:
    async def _build() -> async_sessionmaker:
        engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        return async_sessionmaker(engine, expire_on_commit=False)

    return asyncio.run(_build())


def _cleanup_db(session_factory: async_sessionmaker) -> None:
    async def _cleanup() -> None:
        async with session_factory() as session:
            await session.execute(text("TRUNCATE TABLE opus_jobs, tracks RESTART IDENTITY CASCADE"))
            await session.commit()

    asyncio.run(_cleanup())


def test_enqueue_requeues_stale_processing_job(monkeypatch) -> None:
    session_factory = _build_session_factory()
    _cleanup_db(session_factory)

    async def _run() -> None:
        repo = PostgresOpusJobRepository(session_factory)
        track_id = uuid4()
        first = await repo.enqueue(OpusJobCreate(track_id=track_id, mp3_url="https://audio/old.mp3"))

        async with session_factory() as session:
            job = await session.get(OpusJobModel, first.id)
            assert job is not None
            job.status = "processing"
            job.updated_at = job.updated_at.replace(year=2000)
            await session.commit()

        monkeypatch.setenv("OPUS_JOB_PROCESSING_TIMEOUT_SECONDS", "30")
        result = await repo.enqueue(OpusJobCreate(track_id=track_id, mp3_url="https://audio/new.mp3"))

        assert result.status == "queued"
        async with session_factory() as session:
            refreshed = await session.get(OpusJobModel, first.id)
            assert refreshed is not None
            assert refreshed.status == "queued"
            assert refreshed.error is None

    asyncio.run(_run())


def test_fetch_next_pending_requeues_stale_processing_job(monkeypatch) -> None:
    session_factory = _build_session_factory()
    _cleanup_db(session_factory)

    async def _run() -> None:
        repo = PostgresOpusJobRepository(session_factory)
        track_id = uuid4()
        created = await repo.enqueue(OpusJobCreate(track_id=track_id, mp3_url="https://audio/test.mp3"))

        async with session_factory() as session:
            job = await session.get(OpusJobModel, created.id)
            assert job is not None
            job.status = "processing"
            job.updated_at = job.updated_at.replace(year=2000)
            await session.commit()

        monkeypatch.setenv("OPUS_JOB_PROCESSING_TIMEOUT_SECONDS", "60")
        picked = await repo.fetch_next_pending()

        assert picked is not None
        assert picked.id == created.id
        assert picked.status == "processing"

    asyncio.run(_run())


def test_mark_completed_and_failed_update_status() -> None:
    session_factory = _build_session_factory()
    _cleanup_db(session_factory)

    async def _run() -> None:
        repo = PostgresOpusJobRepository(session_factory)

        completed_job = await repo.enqueue(OpusJobCreate(track_id=uuid4(), mp3_url="https://audio/done.mp3"))
        await repo.mark_completed(job_id=completed_job.id)

        failed_job = await repo.enqueue(OpusJobCreate(track_id=uuid4(), mp3_url="https://audio/fail.mp3"))
        await repo.mark_failed(job_id=failed_job.id, error="boom")

        async with session_factory() as session:
            done = await session.get(OpusJobModel, completed_job.id)
            bad = await session.get(OpusJobModel, failed_job.id)
            assert done is not None and done.status == "completed" and done.error is None
            assert bad is not None and bad.status == "failed" and bad.error == "boom"

    asyncio.run(_run())
