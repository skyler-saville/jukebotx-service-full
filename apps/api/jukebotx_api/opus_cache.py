from __future__ import annotations

import asyncio
import logging
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from typing import Final
from urllib.request import Request, urlopen
from uuid import UUID


logger = logging.getLogger(__name__)

_DOWNLOAD_TIMEOUT_SECONDS: Final = 30


class OpusTranscodeError(RuntimeError):
    pass


class OpusCacheService:
    def __init__(self, *, cache_dir: Path, ttl_seconds: int) -> None:
        self._cache_dir = cache_dir
        self._ttl_seconds = ttl_seconds

    async def ensure_cached(self, *, track_id: UUID, mp3_url: str) -> Path:
        return await asyncio.to_thread(self._ensure_cached_sync, track_id, mp3_url)

    def _ensure_cached_sync(self, track_id: UUID, mp3_url: str) -> Path:
        cache_path = self._cache_dir / f"{track_id}.opus"
        if cache_path.exists() and self._is_fresh(cache_path):
            return cache_path

        self._cache_dir.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory(prefix="jukebotx-opus-") as tmp_dir:
            tmp_path = Path(tmp_dir)
            mp3_path = tmp_path / "input.mp3"
            output_path = tmp_path / "output.opus"

            self._download_mp3(mp3_url, mp3_path)
            self._run_ffmpeg(mp3_path, output_path)
            shutil.move(str(output_path), cache_path)

        return cache_path

    def _is_fresh(self, path: Path) -> bool:
        if self._ttl_seconds <= 0:
            return True
        try:
            age = time.time() - path.stat().st_mtime
        except FileNotFoundError:
            return False
        return age < self._ttl_seconds

    def _download_mp3(self, url: str, destination: Path) -> None:
        logger.info("Downloading MP3 for Opus cache: %s", url)
        request = Request(url, headers={"User-Agent": "jukebotx-opus-cache"})
        try:
            with urlopen(request, timeout=_DOWNLOAD_TIMEOUT_SECONDS) as response:
                with destination.open("wb") as handle:
                    shutil.copyfileobj(response, handle)
        except Exception as exc:
            raise OpusTranscodeError(f"Failed to download MP3 from {url}") from exc

    def _run_ffmpeg(self, mp3_path: Path, output_path: Path) -> None:
        command = [
            "ffmpeg",
            "-y",
            "-i",
            str(mp3_path),
            "-c:a",
            "libopus",
            "-b:a",
            "128k",
            "-f",
            "opus",
            str(output_path),
        ]
        logger.info("Transcoding MP3 to Opus: %s", " ".join(command))
        try:
            subprocess.run(command, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as exc:
            logger.error("ffmpeg failed: %s", exc.stderr)
            raise OpusTranscodeError("ffmpeg failed to transcode MP3") from exc
