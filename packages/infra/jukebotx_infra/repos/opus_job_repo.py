from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from jukebotx_core.ports.repositories import OpusJob, OpusJobCreate, OpusJobRepository
from jukebotx_infra.db.models import OpusJobModel


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _to_domain(job: OpusJobModel) -> OpusJob:
    return OpusJob(
        id=job.id,
        track_id=job.track_id,
        mp3_url=job.mp3_url,
        status=job.status,
        error=job.error,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


class PostgresOpusJobRepository(OpusJobRepository):
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def get_by_track_id(self, *, track_id: UUID) -> OpusJob | None:
        async with self._session_factory() as session:
            job = await session.scalar(select(OpusJobModel).where(OpusJobModel.track_id == track_id))
            return _to_domain(job) if job else None

    async def enqueue(self, data: OpusJobCreate) -> OpusJob:
        async with self._session_factory() as session:
            async with session.begin():
                job = await session.scalar(select(OpusJobModel).where(OpusJobModel.track_id == data.track_id))
                if job is None:
                    job = OpusJobModel(
                        track_id=data.track_id,
                        mp3_url=data.mp3_url,
                        status="queued",
                        created_at=_now(),
                        updated_at=_now(),
                    )
                    session.add(job)
                    await session.flush()
                    return _to_domain(job)

                if job.status != "processing":
                    job.mp3_url = data.mp3_url
                    job.status = "queued"
                    job.error = None
                    job.updated_at = _now()
                return _to_domain(job)

    async def fetch_next_pending(self) -> OpusJob | None:
        async with self._session_factory() as session:
            async with session.begin():
                result = await session.scalars(
                    select(OpusJobModel)
                    .where(OpusJobModel.status == "queued")
                    .order_by(OpusJobModel.created_at.asc())
                    .with_for_update(skip_locked=True)
                    .limit(1)
                )
                job = result.first()
                if job is None:
                    return None
                job.status = "processing"
                job.updated_at = _now()
                await session.flush()
                return _to_domain(job)

    async def mark_completed(self, *, job_id: UUID) -> None:
        async with self._session_factory() as session:
            result = await session.execute(
                update(OpusJobModel)
                .where(OpusJobModel.id == job_id)
                .values(status="completed", error=None, updated_at=_now())
            )
            await session.commit()
            if result.rowcount == 0:
                raise KeyError(f"Opus job not found: {job_id}")

    async def mark_failed(self, *, job_id: UUID, error: str) -> None:
        async with self._session_factory() as session:
            result = await session.execute(
                update(OpusJobModel)
                .where(OpusJobModel.id == job_id)
                .values(status="failed", error=error, updated_at=_now())
            )
            await session.commit()
            if result.rowcount == 0:
                raise KeyError(f"Opus job not found: {job_id}")
