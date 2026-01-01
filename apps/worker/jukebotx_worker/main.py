from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from jukebotx_infra.opus_cache import OpusCacheService
from jukebotx_infra.repos.opus_job_repo import PostgresOpusJobRepository

from jukebotx_worker.settings import load_worker_settings
from jukebotx_worker.transcode import OpusTranscodeError, OpusTranscoder


logger = logging.getLogger(__name__)


async def _process_job(
    *,
    job_repo: PostgresOpusJobRepository,
    cache: OpusCacheService,
    transcoder: OpusTranscoder,
) -> bool:
    job = await job_repo.fetch_next_pending()
    if job is None:
        return False

    output_path = cache.cache_path(track_id=job.track_id)
    if output_path.exists() and cache.is_fresh(output_path):
        logger.info("Opus cache already fresh for track %s", job.track_id)
        await job_repo.mark_completed(job_id=job.id)
        return True

    cache.ensure_cache_dir()

    try:
        await asyncio.to_thread(transcoder.transcode, mp3_url=job.mp3_url, output_path=output_path)
    except OpusTranscodeError as exc:
        logger.error("Opus transcode failed for track %s: %s", job.track_id, exc)
        await job_repo.mark_failed(job_id=job.id, error=str(exc))
        return True

    await job_repo.mark_completed(job_id=job.id)
    logger.info("Opus transcode completed for track %s", job.track_id)
    return True


async def run_worker() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = load_worker_settings()
    if settings.database_url:
        os.environ["DATABASE_URL"] = settings.database_url

    from jukebotx_infra.db import async_session_factory, init_db

    cache_dir = Path(settings.opus_cache_dir)
    cache = OpusCacheService(cache_dir=cache_dir, ttl_seconds=settings.opus_cache_ttl_seconds)
    transcoder = OpusTranscoder(ffmpeg_path=settings.opus_ffmpeg_path)
    job_repo = PostgresOpusJobRepository(async_session_factory)

    await init_db()

    logger.info("Opus worker started. Poll interval=%.2fs", settings.opus_job_poll_seconds)

    while True:
        try:
            processed = await _process_job(job_repo=job_repo, cache=cache, transcoder=transcoder)
            if not processed:
                await asyncio.sleep(settings.opus_job_poll_seconds)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Worker loop error")
            await asyncio.sleep(settings.opus_job_poll_seconds)


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
