from pathlib import Path
import sys
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.extend([str(ROOT / "apps" / "worker")])

import jukebotx_worker.transcode as transcode
from jukebotx_worker.transcode import OpusTranscodeError, OpusTranscoder


def test_download_mp3_wraps_network_errors(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    transcoder = OpusTranscoder(ffmpeg_path="ffmpeg", download_timeout_seconds=13, bitrate_kbps=128)

    def _raise(*args, **kwargs):
        raise TimeoutError("network down")

    monkeypatch.setattr(transcode, "urlopen", _raise)

    with pytest.raises(OpusTranscodeError, match="Failed to download MP3"):
        transcoder._download_mp3("https://example.com/test.mp3", tmp_path / "out.mp3")


def test_download_mp3_uses_configured_timeout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    transcoder = OpusTranscoder(ffmpeg_path="ffmpeg", download_timeout_seconds=17, bitrate_kbps=128)
    captured_timeout = {"value": None}

    def _fake_urlopen(request, timeout):
        captured_timeout["value"] = timeout
        raise TimeoutError("expected")

    monkeypatch.setattr(transcode, "urlopen", _fake_urlopen)

    with pytest.raises(OpusTranscodeError):
        transcoder._download_mp3("https://example.com/test.mp3", tmp_path / "out.mp3")

    assert captured_timeout["value"] == 17


def test_run_ffmpeg_wraps_called_process_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    transcoder = OpusTranscoder(ffmpeg_path="ffmpeg", download_timeout_seconds=30, bitrate_kbps=192)

    def _raise(*args, **kwargs):
        raise subprocess.CalledProcessError(returncode=1, cmd="ffmpeg", stderr="bad input")

    monkeypatch.setattr(subprocess, "run", _raise)

    with pytest.raises(OpusTranscodeError, match="ffmpeg failed"):
        transcoder._run_ffmpeg(tmp_path / "in.mp3", tmp_path / "out.opus")


def test_run_ffmpeg_uses_configured_bitrate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    transcoder = OpusTranscoder(ffmpeg_path="ffmpeg", download_timeout_seconds=30, bitrate_kbps=96)
    captured = {"cmd": None}

    def _run(command, check, capture_output, text):
        captured["cmd"] = command

    monkeypatch.setattr(subprocess, "run", _run)

    transcoder._run_ffmpeg(tmp_path / "in.mp3", tmp_path / "out.opus")

    assert captured["cmd"] is not None
    assert "96k" in captured["cmd"]


def test_transcode_bubbles_download_failures(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    transcoder = OpusTranscoder(ffmpeg_path="ffmpeg", download_timeout_seconds=30, bitrate_kbps=128)

    def _fail_download(*args, **kwargs):
        raise OpusTranscodeError("Failed to download MP3 from https://bad")

    monkeypatch.setattr(OpusTranscoder, "_download_mp3", _fail_download)

    with pytest.raises(OpusTranscodeError, match="Failed to download MP3"):
        transcoder.transcode(mp3_url="https://bad", output_path=tmp_path / "result.opus")
