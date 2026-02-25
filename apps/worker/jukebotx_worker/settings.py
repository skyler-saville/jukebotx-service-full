from __future__ import annotations

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class WorkerSettings:
    database_url: str
    opus_cache_dir: str
    opus_cache_ttl_seconds: int
    opus_ffmpeg_path: str
    opus_download_timeout_seconds: int
    opus_bitrate_kbps: int
    opus_job_poll_seconds: float
    opus_job_max_retries: int
    opus_job_retry_backoff_seconds: float | None
    opus_job_retry_backoff_multiplier: float | None
    opus_job_retry_max_backoff_seconds: float | None
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


def load_worker_settings() -> WorkerSettings:
    backoff_seconds = os.environ.get("OPUS_JOB_RETRY_BACKOFF_SECONDS")
    backoff_multiplier = os.environ.get("OPUS_JOB_RETRY_BACKOFF_MULTIPLIER")
    backoff_max_seconds = os.environ.get("OPUS_JOB_RETRY_MAX_BACKOFF_SECONDS")

    return WorkerSettings(
        database_url=os.environ.get("DATABASE_URL", ""),
        opus_cache_dir=os.environ.get("OPUS_CACHE_DIR", "static/opus"),
        opus_cache_ttl_seconds=int(os.environ.get("OPUS_CACHE_TTL_SECONDS", "604800")),
        opus_ffmpeg_path=os.environ.get("OPUS_FFMPEG_PATH", "ffmpeg"),
        opus_download_timeout_seconds=int(os.environ.get("OPUS_DOWNLOAD_TIMEOUT_SECONDS", "30")),
        opus_bitrate_kbps=int(os.environ.get("OPUS_BITRATE_KBPS", "128")),
        opus_job_poll_seconds=float(os.environ.get("OPUS_JOB_POLL_SECONDS", "2.5")),
        opus_job_max_retries=max(int(os.environ.get("OPUS_JOB_MAX_RETRIES", "3")), 0),
        opus_job_retry_backoff_seconds=float(backoff_seconds) if backoff_seconds else None,
        opus_job_retry_backoff_multiplier=float(backoff_multiplier) if backoff_multiplier else None,
        opus_job_retry_max_backoff_seconds=float(backoff_max_seconds) if backoff_max_seconds else None,
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
    )
