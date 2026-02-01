from __future__ import annotations

import logging
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Final
from urllib.request import Request, urlopen


logger = logging.getLogger(__name__)

_DOWNLOAD_TIMEOUT_SECONDS: Final = 30


class OpusTranscodeError(RuntimeError):
    pass


class OpusTranscoder:
    def __init__(self, *, ffmpeg_path: str) -> None:
        self._ffmpeg_path = ffmpeg_path

    def transcode(self, *, mp3_url: str, output_path: Path) -> None:
        with tempfile.TemporaryDirectory(prefix="jukebotx-opus-") as tmp_dir:
            tmp_path = Path(tmp_dir)
            mp3_path = tmp_path / "input.mp3"
            output_tmp_path = tmp_path / "output.opus"

            self._download_mp3(mp3_url, mp3_path)
            self._run_ffmpeg(mp3_path, output_tmp_path)
            shutil.move(str(output_tmp_path), output_path)

    def _download_mp3(self, url: str, destination: Path) -> None:
        logger.info("Downloading MP3 for Opus cache: %s", url)
        request = Request(url, headers={"User-Agent": "jukebotx-opus-worker"})
        try:
            with urlopen(request, timeout=_DOWNLOAD_TIMEOUT_SECONDS) as response:
                with destination.open("wb") as handle:
                    shutil.copyfileobj(response, handle)
        except Exception as exc:
            raise OpusTranscodeError(f"Failed to download MP3 from {url}") from exc

    def _run_ffmpeg(self, mp3_path: Path, output_path: Path) -> None:
        command = [
            self._ffmpeg_path,
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
