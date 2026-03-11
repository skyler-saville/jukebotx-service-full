from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from datetime import datetime, timedelta, timezone
import shutil
import subprocess
import tempfile
from urllib.request import Request, urlopen

from jukebotx_infra.opus_cache import OpusCacheService
from jukebotx_infra.repos.opus_job_repo import PostgresOpusJobRepository
from jukebotx_infra.repos.track_repo import PostgresTrackRepository
from jukebotx_infra.storage import OpusStorageConfig, OpusStorageService
from jukebotx_infra.suno import BrowserSunoMediaClient

from jukebotx_worker.settings import load_worker_settings
from jukebotx_worker.transcode import OpusTranscodeError, OpusTranscoder


logger = logging.getLogger(__name__)
_DOWNLOAD_TIMEOUT_SECONDS = 30

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
    web_audio_output_path = cache.cache_path_with_extension(track_id=job.track_id, extension="ogg")
    opus_url: str | None = None
    opus_path: str | None = None
    web_audio_url: str | None = None
    web_audio_path: str | None = None
    has_fresh_opus = False
    has_fresh_web_audio = False

    if storage.is_enabled:
        object_key = storage.object_key(track_id=job.track_id)
        web_audio_object_key = storage.object_key_for_extension(
            track_id=job.track_id,
            extension="ogg",
            suffix="web",
        )
        has_fresh_opus = storage.is_fresh(object_key=object_key)
        has_fresh_web_audio = storage.is_fresh(object_key=web_audio_object_key)
        opus_url = storage.public_url(object_key=object_key)
        opus_path = object_key
        web_audio_url = storage.public_url(object_key=web_audio_object_key)
        web_audio_path = web_audio_object_key
    else:
        has_fresh_opus = output_path.exists() and cache.is_fresh(output_path)
        has_fresh_web_audio = web_audio_output_path.exists() and cache.is_fresh(web_audio_output_path)
        opus_url = f"/tracks/{job.track_id}/opus"
        opus_path = str(output_path)
        web_audio_url = f"/tracks/{job.track_id}/web-audio"
        web_audio_path = str(web_audio_output_path)

    if has_fresh_opus and has_fresh_web_audio:
        logger.info("Audio artifacts already fresh for track %s", job.track_id)
        await job_repo.mark_completed(job_id=job.id)
        await track_repo.update_opus_metadata(
            track_id=job.track_id,
            opus_url=opus_url,
            opus_path=opus_path,
            opus_status="completed",
            opus_transcoded_at=_now(),
        )
        await track_repo.update_web_audio_metadata(
            track_id=job.track_id,
            web_audio_url=web_audio_url,
            web_audio_path=web_audio_path,
            web_audio_status="completed",
            web_audio_transcoded_at=_now(),
        )
        return True

    cache.ensure_cache_dir()

    if not has_fresh_opus:
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
            await track_repo.update_web_audio_metadata(
                track_id=job.track_id,
                web_audio_url=None,
                web_audio_path=None,
                web_audio_status="failed",
                web_audio_transcoded_at=_now(),
            )
            return True

        if storage.is_enabled:
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
            logger.info("Opus transcode uploaded to storage for track %s", job.track_id)
        else:
            logger.info("Opus transcode completed for track %s", job.track_id)

    await track_repo.update_opus_metadata(
        track_id=job.track_id,
        opus_url=opus_url,
        opus_path=opus_path,
        opus_status="completed",
        opus_transcoded_at=_now(),
    )

    if has_fresh_web_audio:
        await track_repo.update_web_audio_metadata(
            track_id=job.track_id,
            web_audio_url=web_audio_url,
            web_audio_path=web_audio_path,
            web_audio_status="completed",
            web_audio_transcoded_at=_now(),
        )
    else:
        try:
            await asyncio.to_thread(
                transcoder.transcode_web_audio,
                mp3_url=job.mp3_url,
                output_path=web_audio_output_path,
            )
            if storage.is_enabled:
                storage.upload_media_file(
                    local_path=web_audio_output_path,
                    object_key=web_audio_object_key,
                    content_type="audio/ogg",
                )
                try:
                    web_audio_output_path.unlink()
                except FileNotFoundError:
                    pass
            await track_repo.update_web_audio_metadata(
                track_id=job.track_id,
                web_audio_url=web_audio_url,
                web_audio_path=web_audio_path,
                web_audio_status="completed",
                web_audio_transcoded_at=_now(),
            )
            logger.info("Web audio generated for track %s", job.track_id)
        except Exception as exc:  # noqa: BLE001 - keep opus pipeline resilient
            logger.warning("Web audio generation failed for track %s: %s", job.track_id, exc)
            await track_repo.update_web_audio_metadata(
                track_id=job.track_id,
                web_audio_url=None,
                web_audio_path=None,
                web_audio_status="failed",
                web_audio_transcoded_at=_now(),
            )

    await job_repo.mark_completed(job_id=job.id)
    return True


async def _process_media_backfill(
    *,
    track_repo: PostgresTrackRepository,
    media_client: BrowserSunoMediaClient,
    min_track_age_seconds: int,
) -> bool:
    stale_before = _now() - timedelta(seconds=min_track_age_seconds)
    track = await track_repo.fetch_next_missing_media(stale_before=stale_before)
    if track is None:
        return False

    try:
        metadata = await media_client.fetch_media(track.suno_url)
    except Exception as exc:  # noqa: BLE001 - worker loop should remain resilient
        logger.warning("Media backfill failed for track %s (%s): %s", track.id, track.suno_url, exc)
        await track_repo.mark_media_backfill_attempted(track_id=track.id)
        return True

    if metadata.image_url is None and metadata.video_url is None:
        logger.info("Media backfill found no media for track %s", track.id)
        await track_repo.mark_media_backfill_attempted(track_id=track.id)
        return True

    await track_repo.update_media_metadata(
        track_id=track.id,
        image_url=metadata.image_url,
        video_url=metadata.video_url,
    )
    logger.info(
        "Media backfill updated track %s (image=%s video=%s)",
        track.id,
        bool(metadata.image_url),
        bool(metadata.video_url),
    )
    return True


def _download_remote_file(*, url: str, destination: Path) -> None:
    request = Request(url, headers={"User-Agent": "jukebotx-media-worker/1.0"})
    with urlopen(request, timeout=_DOWNLOAD_TIMEOUT_SECONDS) as response:
        with destination.open("wb") as handle:
            shutil.copyfileobj(response, handle)


def _transcode_video_to_gif(
    *,
    ffmpeg_path: str,
    input_path: Path,
    output_path: Path,
    fps: int,
    width: int,
) -> None:
    filter_graph = f"fps={fps},scale={width}:-1:flags=lanczos"
    command = [
        ffmpeg_path,
        "-y",
        "-i",
        str(input_path),
        "-vf",
        filter_graph,
        "-loop",
        "0",
        str(output_path),
    ]
    subprocess.run(command, check=True, capture_output=True, text=True)


async def _process_media_gif_backfill(
    *,
    track_repo: PostgresTrackRepository,
    storage: OpusStorageService,
    min_track_age_seconds: int,
    ffmpeg_path: str,
    fps: int,
    width: int,
    storage_prefix: str,
) -> bool:
    stale_before = _now() - timedelta(seconds=min_track_age_seconds)
    track = await track_repo.fetch_next_video_missing_gif(stale_before=stale_before)
    if track is None:
        return False
    if track.video_url is None:
        await track_repo.mark_media_backfill_attempted(track_id=track.id)
        return True
    if not storage.is_enabled:
        logger.warning("MEDIA_GIF_ENABLED is true but storage is not configured; skipping GIF conversion.")
        await track_repo.mark_media_backfill_attempted(track_id=track.id)
        return True

    with tempfile.TemporaryDirectory(prefix="jukebotx-gif-") as tmp_dir:
        tmp_path = Path(tmp_dir)
        input_path = tmp_path / "input.mp4"
        output_path = tmp_path / "output.gif"

        try:
            await asyncio.to_thread(_download_remote_file, url=track.video_url, destination=input_path)
            await asyncio.to_thread(
                _transcode_video_to_gif,
                ffmpeg_path=ffmpeg_path,
                input_path=input_path,
                output_path=output_path,
                fps=fps,
                width=width,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("GIF conversion failed for track %s: %s", track.id, exc)
            await track_repo.mark_media_backfill_attempted(track_id=track.id)
            return True

        object_key = f"{storage_prefix.strip('/')}/{track.id}.gif"
        try:
            storage.upload_media_file(
                local_path=output_path,
                object_key=object_key,
                content_type="image/gif",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("GIF upload failed for track %s: %s", track.id, exc)
            await track_repo.mark_media_backfill_attempted(track_id=track.id)
            return True

    gif_url = storage.public_url(object_key=object_key) or storage.get_access_url(object_key=object_key)
    await track_repo.update_media_metadata(
        track_id=track.id,
        image_url=gif_url,
        video_url=track.video_url,
    )
    logger.info("GIF media generated for track %s", track.id)
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
    media_client: BrowserSunoMediaClient | None = None

    await init_db()

    logger.info("Opus worker started. Poll interval=%.2fs", settings.opus_job_poll_seconds)
    if settings.media_backfill_enabled:
        media_client = BrowserSunoMediaClient(
            timeout_seconds=settings.media_backfill_browser_timeout_seconds,
            user_agent=settings.media_backfill_user_agent,
        )
        try:
            await media_client.start()
            logger.info(
                "Media backfill enabled. Poll interval=%.2fs min_track_age=%ss",
                settings.media_backfill_poll_seconds,
                settings.media_backfill_min_track_age_seconds,
            )
        except Exception:
            logger.exception("Disabling media backfill; browser startup failed.")
            media_client = None

    next_media_poll_at = asyncio.get_running_loop().time()
    next_media_gif_poll_at = asyncio.get_running_loop().time()

    try:
        while True:
            try:
                processed = await _process_job(
                    job_repo=job_repo,
                    cache=cache,
                    storage=storage,
                    transcoder=transcoder,
                    track_repo=track_repo,
                )

                now_monotonic = asyncio.get_running_loop().time()
                media_processed = False
                media_gif_processed = False
                if (
                    media_client is not None
                    and now_monotonic >= next_media_poll_at
                ):
                    media_processed = await _process_media_backfill(
                        track_repo=track_repo,
                        media_client=media_client,
                        min_track_age_seconds=settings.media_backfill_min_track_age_seconds,
                    )
                    next_media_poll_at = now_monotonic + settings.media_backfill_poll_seconds

                if settings.media_gif_enabled and now_monotonic >= next_media_gif_poll_at:
                    media_gif_processed = await _process_media_gif_backfill(
                        track_repo=track_repo,
                        storage=storage,
                        min_track_age_seconds=settings.media_gif_min_track_age_seconds,
                        ffmpeg_path=settings.media_gif_ffmpeg_path,
                        fps=settings.media_gif_fps,
                        width=settings.media_gif_width,
                        storage_prefix=settings.media_gif_storage_prefix,
                    )
                    next_media_gif_poll_at = now_monotonic + settings.media_gif_poll_seconds

                if not processed and not media_processed and not media_gif_processed:
                    await asyncio.sleep(settings.opus_job_poll_seconds)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Worker loop error")
                await asyncio.sleep(settings.opus_job_poll_seconds)
    finally:
        if media_client is not None:
            await media_client.close()


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
