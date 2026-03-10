from __future__ import annotations

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class WorkerSettings:
    database_url: str
    opus_cache_dir: str
    opus_cache_ttl_seconds: int
    opus_ffmpeg_path: str
    opus_job_poll_seconds: float
    opus_storage_provider: str
    opus_storage_bucket: str
    opus_storage_prefix: str
    opus_storage_region: str
    opus_storage_endpoint_url: str
    opus_storage_access_key_id: str
    opus_storage_secret_access_key: str
    opus_storage_public_base_url: str
    opus_storage_signed_url_ttl_seconds: int
    opus_storage_ttl_seconds: int
    media_backfill_enabled: bool
    media_backfill_poll_seconds: float
    media_backfill_min_track_age_seconds: int
    media_backfill_browser_timeout_seconds: float
    media_backfill_user_agent: str
    media_gif_enabled: bool
    media_gif_poll_seconds: float
    media_gif_min_track_age_seconds: int
    media_gif_ffmpeg_path: str
    media_gif_fps: int
    media_gif_width: int
    media_gif_storage_prefix: str


def load_worker_settings() -> WorkerSettings:
    return WorkerSettings(
        database_url=os.environ.get("DATABASE_URL", ""),
        opus_cache_dir=os.environ.get("OPUS_CACHE_DIR", "static/opus"),
        opus_cache_ttl_seconds=int(os.environ.get("OPUS_CACHE_TTL_SECONDS", "604800")),
        opus_ffmpeg_path=os.environ.get("OPUS_FFMPEG_PATH", "ffmpeg"),
        opus_job_poll_seconds=float(os.environ.get("OPUS_JOB_POLL_SECONDS", "2.5")),
        opus_storage_provider=os.environ.get("OPUS_STORAGE_PROVIDER", "s3"),
        opus_storage_bucket=os.environ.get("OPUS_STORAGE_BUCKET", ""),
        opus_storage_prefix=os.environ.get("OPUS_STORAGE_PREFIX", "opus"),
        opus_storage_region=os.environ.get("OPUS_STORAGE_REGION", ""),
        opus_storage_endpoint_url=os.environ.get("OPUS_STORAGE_ENDPOINT_URL", ""),
        opus_storage_access_key_id=os.environ.get("OPUS_STORAGE_ACCESS_KEY_ID", ""),
        opus_storage_secret_access_key=os.environ.get("OPUS_STORAGE_SECRET_ACCESS_KEY", ""),
        opus_storage_public_base_url=os.environ.get("OPUS_STORAGE_PUBLIC_BASE_URL", ""),
        opus_storage_signed_url_ttl_seconds=int(
            os.environ.get("OPUS_STORAGE_SIGNED_URL_TTL_SECONDS", "900")
        ),
        opus_storage_ttl_seconds=int(os.environ.get("OPUS_STORAGE_TTL_SECONDS", "604800")),
        media_backfill_enabled=os.environ.get("MEDIA_BACKFILL_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"},
        media_backfill_poll_seconds=float(os.environ.get("MEDIA_BACKFILL_POLL_SECONDS", "30")),
        media_backfill_min_track_age_seconds=int(os.environ.get("MEDIA_BACKFILL_MIN_TRACK_AGE_SECONDS", "600")),
        media_backfill_browser_timeout_seconds=float(
            os.environ.get("MEDIA_BACKFILL_BROWSER_TIMEOUT_SECONDS", "25")
        ),
        media_backfill_user_agent=os.environ.get(
            "MEDIA_BACKFILL_USER_AGENT",
            "jukebotx-media-worker/1.0",
        ),
        media_gif_enabled=os.environ.get("MEDIA_GIF_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"},
        media_gif_poll_seconds=float(os.environ.get("MEDIA_GIF_POLL_SECONDS", "45")),
        media_gif_min_track_age_seconds=int(os.environ.get("MEDIA_GIF_MIN_TRACK_AGE_SECONDS", "600")),
        media_gif_ffmpeg_path=os.environ.get("MEDIA_GIF_FFMPEG_PATH", os.environ.get("OPUS_FFMPEG_PATH", "ffmpeg")),
        media_gif_fps=int(os.environ.get("MEDIA_GIF_FPS", "10")),
        media_gif_width=int(os.environ.get("MEDIA_GIF_WIDTH", "512")),
        media_gif_storage_prefix=os.environ.get("MEDIA_GIF_STORAGE_PREFIX", "media/gif"),
    )
