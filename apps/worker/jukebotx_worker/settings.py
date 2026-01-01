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


def load_worker_settings() -> WorkerSettings:
    return WorkerSettings(
        database_url=os.environ.get("DATABASE_URL", ""),
        opus_cache_dir=os.environ.get("OPUS_CACHE_DIR", "static/opus"),
        opus_cache_ttl_seconds=int(os.environ.get("OPUS_CACHE_TTL_SECONDS", "604800")),
        opus_ffmpeg_path=os.environ.get("OPUS_FFMPEG_PATH", "ffmpeg"),
        opus_job_poll_seconds=float(os.environ.get("OPUS_JOB_POLL_SECONDS", "2.5")),
    )
