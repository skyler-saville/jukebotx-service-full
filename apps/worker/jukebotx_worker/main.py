from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from datetime import datetime, timezone

from jukebotx_infra.opus_cache import OpusCacheService
from jukebotx_infra.repos.opus_job_repo import PostgresOpusJobRepository
from jukebotx_infra.repos.track_repo import PostgresTrackRepository
from jukebotx_infra.storage import OpusStorageConfig, OpusStorageService

from jukebotx_worker.settings import load_worker_settings
from jukebotx_worker.transcode import OpusTranscodeError, OpusTranscoder


logger = logging.getLogger(__name__)

def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _process_job(
    *,
    job_repo: PostgresOpusJobRepository,
    cache: OpusCacheService,
    storage: OpusStorageService,
    transcoder: OpusTranscoder,
    track_repo: PostgresTrackRepository,
) -> bool:
    job = await job_repo.fetch_next_pending()
    if job is None:
        return False

    output_path = cache.cache_path(track_id=job.track_id)
    if storage.is_enabled:
        object_key = storage.object_key(track_id=job.track_id)
        if storage.is_fresh(object_key=object_key):
            logger.info("Opus storage already fresh for track %s", job.track_id)
            await job_repo.mark_completed(job_id=job.id)
            await track_repo.update_opus_metadata(
                track_id=job.track_id,
                opus_url=storage.public_url(object_key=object_key),
                opus_path=object_key,
                opus_status="completed",
                opus_transcoded_at=_now(),
            )
            return True
    else:
        if output_path.exists() and cache.is_fresh(output_path):
            logger.info("Opus cache already fresh for track %s", job.track_id)
            await job_repo.mark_completed(job_id=job.id)
            await track_repo.update_opus_metadata(
                track_id=job.track_id,
                opus_url=f"/tracks/{job.track_id}/opus",
                opus_path=str(output_path),
                opus_status="completed",
                opus_transcoded_at=_now(),
            )
            return True

    cache.ensure_cache_dir()

    try:
        await asyncio.to_thread(transcoder.transcode, mp3_url=job.mp3_url, output_path=output_path)
    except OpusTranscodeError as exc:
        logger.error("Opus transcode failed for track %s: %s", job.track_id, exc)
        await job_repo.mark_failed(job_id=job.id, error=str(exc))
        await track_repo.update_opus_metadata(
            track_id=job.track_id,
            opus_url=None,
            opus_path=None,
            opus_status="failed",
            opus_transcoded_at=_now(),
        )
        return True

    if storage.is_enabled:
        object_key = storage.object_key(track_id=job.track_id)
        try:
            storage.upload_file(local_path=output_path, object_key=object_key)
        except Exception as exc:  # noqa: BLE001 - log and mark failed
            logger.error("Opus upload failed for track %s: %s", job.track_id, exc)
            await job_repo.mark_failed(job_id=job.id, error=str(exc))
            await track_repo.update_opus_metadata(
                track_id=job.track_id,
                opus_url=None,
                opus_path=None,
                opus_status="failed",
                opus_transcoded_at=_now(),
            )
            return True
        try:
            output_path.unlink()
        except FileNotFoundError:
            pass
        opus_url = storage.public_url(object_key=object_key)
        await job_repo.mark_completed(job_id=job.id)
        await track_repo.update_opus_metadata(
            track_id=job.track_id,
            opus_url=opus_url,
            opus_path=object_key,
            opus_status="completed",
            opus_transcoded_at=_now(),
        )
        logger.info("Opus transcode uploaded to storage for track %s", job.track_id)
        return True

    await job_repo.mark_completed(job_id=job.id)
    await track_repo.update_opus_metadata(
        track_id=job.track_id,
        opus_url=f"/tracks/{job.track_id}/opus",
        opus_path=str(output_path),
        opus_status="completed",
        opus_transcoded_at=_now(),
    )
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
    storage = OpusStorageService(
        OpusStorageConfig(
            provider=settings.opus_storage_provider,
            bucket=settings.opus_storage_bucket,
            prefix=settings.opus_storage_prefix,
            region=settings.opus_storage_region,
            endpoint_url=settings.opus_storage_endpoint_url,
            access_key_id=settings.opus_storage_access_key_id,
            secret_access_key=settings.opus_storage_secret_access_key,
            public_base_url=settings.opus_storage_public_base_url,
            signed_url_ttl_seconds=settings.opus_storage_signed_url_ttl_seconds,
            ttl_seconds=settings.opus_storage_ttl_seconds,
        )
    )
    transcoder = OpusTranscoder(ffmpeg_path=settings.opus_ffmpeg_path)
    job_repo = PostgresOpusJobRepository(async_session_factory)
    track_repo = PostgresTrackRepository(async_session_factory)

    await init_db()

    logger.info("Opus worker started. Poll interval=%.2fs", settings.opus_job_poll_seconds)

    while True:
        try:
            processed = await _process_job(
                job_repo=job_repo,
                cache=cache,
                storage=storage,
                transcoder=transcoder,
                track_repo=track_repo,
            )
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
