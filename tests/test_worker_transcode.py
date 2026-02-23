from pathlib import Path
import sys
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.extend([str(ROOT / "apps" / "worker")])

import jukebotx_worker.transcode as transcode
from jukebotx_worker.transcode import OpusTranscodeError, OpusTranscoder


def test_download_mp3_wraps_network_errors(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    transcoder = OpusTranscoder(ffmpeg_path="ffmpeg")

    def _raise(*args, **kwargs):
        raise TimeoutError("network down")

    monkeypatch.setattr(transcode, "urlopen", _raise)

    with pytest.raises(OpusTranscodeError, match="Failed to download MP3"):
        transcoder._download_mp3("https://example.com/test.mp3", tmp_path / "out.mp3")


def test_run_ffmpeg_wraps_called_process_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    transcoder = OpusTranscoder(ffmpeg_path="ffmpeg")

    def _raise(*args, **kwargs):
        raise subprocess.CalledProcessError(returncode=1, cmd="ffmpeg", stderr="bad input")

    monkeypatch.setattr(subprocess, "run", _raise)

    with pytest.raises(OpusTranscodeError, match="ffmpeg failed"):
        transcoder._run_ffmpeg(tmp_path / "in.mp3", tmp_path / "out.opus")


def test_transcode_bubbles_download_failures(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    transcoder = OpusTranscoder(ffmpeg_path="ffmpeg")

    def _fail_download(*args, **kwargs):
        raise OpusTranscodeError("Failed to download MP3 from https://bad")

    monkeypatch.setattr(OpusTranscoder, "_download_mp3", _fail_download)

    with pytest.raises(OpusTranscodeError, match="Failed to download MP3"):
        transcoder.transcode(mp3_url="https://bad", output_path=tmp_path / "result.opus")
