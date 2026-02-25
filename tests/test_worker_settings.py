from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.extend([str(ROOT / "apps" / "worker")])

from jukebotx_worker.settings import load_worker_settings


def test_load_worker_settings_parses_transcode_fields(monkeypatch) -> None:
    monkeypatch.setenv("OPUS_DOWNLOAD_TIMEOUT_SECONDS", "42")
    monkeypatch.setenv("OPUS_BITRATE_KBPS", "160")
    monkeypatch.setenv("OPUS_JOB_MAX_RETRIES", "5")
    monkeypatch.setenv("OPUS_JOB_RETRY_BACKOFF_SECONDS", "2.5")
    monkeypatch.setenv("OPUS_JOB_RETRY_BACKOFF_MULTIPLIER", "2.0")
    monkeypatch.setenv("OPUS_JOB_RETRY_MAX_BACKOFF_SECONDS", "30")

    settings = load_worker_settings()

    assert settings.opus_download_timeout_seconds == 42
    assert settings.opus_bitrate_kbps == 160
    assert settings.opus_job_max_retries == 5
    assert settings.opus_job_retry_backoff_seconds == 2.5
    assert settings.opus_job_retry_backoff_multiplier == 2.0
    assert settings.opus_job_retry_max_backoff_seconds == 30.0


def test_load_worker_settings_defaults_optional_backoff(monkeypatch) -> None:
    for key in [
        "OPUS_JOB_RETRY_BACKOFF_SECONDS",
        "OPUS_JOB_RETRY_BACKOFF_MULTIPLIER",
        "OPUS_JOB_RETRY_MAX_BACKOFF_SECONDS",
    ]:
        monkeypatch.delenv(key, raising=False)

    settings = load_worker_settings()

    assert settings.opus_job_retry_backoff_seconds is None
    assert settings.opus_job_retry_backoff_multiplier is None
    assert settings.opus_job_retry_max_backoff_seconds is None
    assert settings.opus_job_max_retries >= 0
