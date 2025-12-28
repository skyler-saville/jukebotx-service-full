import subprocess
from pathlib import Path
import sys

import pytest

import discord

ROOT = Path(__file__).resolve().parents[1]
sys.path.extend(
    [
        str(ROOT / "apps" / "bot"),
        str(ROOT / "packages" / "core"),
        str(ROOT / "packages" / "infra"),
    ]
)

from jukebotx_bot.discord.audio import GuildAudioController
from jukebotx_bot.discord.session import SessionState, Track


class FakeStdin:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeProcess:
    def __init__(self, *, raise_timeout: bool = False) -> None:
        self.stdin = FakeStdin()
        self.stderr = None
        self._raise_timeout = raise_timeout
        self.terminated = False
        self.killed = False

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout: float | None = None) -> int:
        if self._raise_timeout and not self.killed:
            raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=timeout or 0)
        return 0


class FakeFFmpegPCMAudio:
    def __init__(self, url: str, *, before_options: str, options: str, stderr) -> None:
        self.url = url
        self.before_options = before_options
        self.options = options
        self.process = FakeProcess(raise_timeout=True)
        self.cleanup_called = False

    def cleanup(self) -> None:
        self.cleanup_called = True


class FakeVoiceClient:
    def __init__(self) -> None:
        self._playing = False
        self._paused = False
        self.stop_called = False
        self.play_calls: list[discord.AudioSource] = []

    def is_playing(self) -> bool:
        return self._playing

    def is_paused(self) -> bool:
        return self._paused

    def play(self, source: discord.AudioSource, after) -> None:
        self._playing = True
        self.play_calls.append(source)
        self.after_callback = after

    def stop(self) -> None:
        self._playing = False
        self.stop_called = True


@pytest.mark.asyncio
async def test_play_next_starts_track(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(discord, "FFmpegPCMAudio", FakeFFmpegPCMAudio)

    session = SessionState()
    session.queue.append(
        Track(url="https://example.com/track1", title="Track 1", requester_id=1, requester_name="User")
    )
    controller = GuildAudioController(guild_id=123, session=session)
    voice_client = FakeVoiceClient()

    started = await controller.play_next(voice_client)

    assert started is not None
    assert started.title == "Track 1"
    assert voice_client.is_playing()
    assert voice_client.play_calls


@pytest.mark.asyncio
async def test_stop_cleans_up_ffmpeg(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(discord, "FFmpegPCMAudio", FakeFFmpegPCMAudio)

    session = SessionState()
    session.queue.append(
        Track(url="https://example.com/track1", title="Track 1", requester_id=1, requester_name="User")
    )
    controller = GuildAudioController(guild_id=123, session=session)
    voice_client = FakeVoiceClient()

    await controller.play_next(voice_client)
    source = controller._current_source
    assert source is not None
    await controller.stop(voice_client)

    assert voice_client.stop_called
    assert controller._current_source is None
    assert source.cleanup_called
    assert source.process.stdin.closed
    assert source.process.terminated
    assert source.process.killed


@pytest.mark.asyncio
async def test_track_end_autoplays_next(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(discord, "FFmpegPCMAudio", FakeFFmpegPCMAudio)

    session = SessionState()
    session.autoplay_enabled = True
    session.queue.append(
        Track(url="https://example.com/track1", title="Track 1", requester_id=1, requester_name="User")
    )
    session.queue.append(
        Track(url="https://example.com/track2", title="Track 2", requester_id=2, requester_name="User2")
    )
    controller = GuildAudioController(guild_id=123, session=session)
    voice_client = FakeVoiceClient()

    first = await controller.play_next(voice_client)
    assert first is not None
    current_source = controller._current_source
    assert current_source is not None

    voice_client._playing = False
    await controller._on_track_end(voice_client, current_source, None)

    assert session.now_playing is not None
    assert session.now_playing.title == "Track 2"
    assert len(voice_client.play_calls) == 2
