from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
import os
from uuid import UUID

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from jukebotx_core.ports.repositories import OpusJob, OpusJobCreate, OpusJobRepository
from jukebotx_infra.db.models import OpusJobModel


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _processing_timeout() -> timedelta:
    seconds = int(os.getenv("OPUS_JOB_PROCESSING_TIMEOUT_SECONDS", "600"))
    return timedelta(seconds=max(seconds, 1))


def _retry_delay_seconds(
    *,
    attempt: int,
    retry_backoff_seconds: float | None,
    retry_backoff_multiplier: float | None,
    retry_max_backoff_seconds: float | None,
) -> float:
    base = retry_backoff_seconds if retry_backoff_seconds is not None else 0.0
    if base <= 0:
        return 0.0

    multiplier = retry_backoff_multiplier if retry_backoff_multiplier is not None else 1.0
    multiplier = max(multiplier, 1.0)
    delay = base * math.pow(multiplier, max(attempt - 1, 0))
    if retry_max_backoff_seconds is not None:
        delay = min(delay, retry_max_backoff_seconds)
    return max(delay, 0.0)


def _to_domain(job: OpusJobModel) -> OpusJob:
    return OpusJob(
        id=job.id,
        track_id=job.track_id,
        mp3_url=job.mp3_url,
        status=job.status,
        error=job.error,
        retry_attempts=job.retry_attempts,
        next_retry_at=job.next_retry_at,
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
                        retry_attempts=0,
                        next_retry_at=None,
                        created_at=_now(),
                        updated_at=_now(),
                    )
                    session.add(job)
                    await session.flush()
                    return _to_domain(job)

                if job.status == "processing":
                    if _now() - job.updated_at > _processing_timeout():
                        job.status = "queued"
                        job.error = None
                        job.next_retry_at = None
                        job.updated_at = _now()
                else:
                    job.mp3_url = data.mp3_url
                    job.status = "queued"
                    job.error = None
                    job.retry_attempts = 0
                    job.next_retry_at = None
                    job.updated_at = _now()
                return _to_domain(job)

    async def fetch_next_pending(self) -> OpusJob | None:
        async with self._session_factory() as session:
            async with session.begin():
                now = _now()
                stale_before = now - _processing_timeout()
                result = await session.scalars(
                    select(OpusJobModel)
                    .where(
                        or_(
                            (OpusJobModel.status == "queued")
                            & (
                                (OpusJobModel.next_retry_at.is_(None))
                                | (OpusJobModel.next_retry_at <= now)
                            ),
                            (OpusJobModel.status == "processing")
                            & (OpusJobModel.updated_at < stale_before),
                        )
                    )
                    .order_by(OpusJobModel.created_at.asc())
                    .with_for_update(skip_locked=True)
                    .limit(1)
                )
                job = result.first()
                if job is None:
                    return None
                job.status = "processing"
                job.updated_at = now
                await session.flush()
                return _to_domain(job)

    async def mark_completed(self, *, job_id: UUID) -> None:
        async with self._session_factory() as session:
            result = await session.execute(
                update(OpusJobModel)
                .where(OpusJobModel.id == job_id)
                .values(status="completed", error=None, next_retry_at=None, updated_at=_now())
            )
            await session.commit()
            if result.rowcount == 0:
                raise KeyError(f"Opus job not found: {job_id}")

    async def mark_failed(
        self,
        *,
        job_id: UUID,
        error: str,
        max_retries: int = 0,
        retry_backoff_seconds: float | None = None,
        retry_backoff_multiplier: float | None = None,
        retry_max_backoff_seconds: float | None = None,
    ) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                job = await session.get(OpusJobModel, job_id, with_for_update=True)
                if job is None:
                    raise KeyError(f"Opus job not found: {job_id}")

                attempt = job.retry_attempts + 1
                job.error = error
                job.retry_attempts = attempt
                job.updated_at = _now()

                if attempt <= max(max_retries, 0):
                    delay_seconds = _retry_delay_seconds(
                        attempt=attempt,
                        retry_backoff_seconds=retry_backoff_seconds,
                        retry_backoff_multiplier=retry_backoff_multiplier,
                        retry_max_backoff_seconds=retry_max_backoff_seconds,
                    )
                    job.status = "queued"
                    job.next_retry_at = job.updated_at + timedelta(seconds=delay_seconds)
                    return

                job.status = "failed"
                job.next_retry_at = None
