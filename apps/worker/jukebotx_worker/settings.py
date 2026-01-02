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
    )
