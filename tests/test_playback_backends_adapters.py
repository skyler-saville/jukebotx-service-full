from pathlib import Path
import asyncio
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.extend(
    [
        str(ROOT / "apps" / "bot"),
        str(ROOT / "packages" / "core"),
        str(ROOT / "packages" / "infra"),
    ]
)

from jukebotx_bot.voice.backends.discord_ffmpeg import DiscordFFmpegPlaybackBackend
from jukebotx_bot.voice.backends.lavalink import LavalinkPlaybackBackend


class FakeFFmpegPCMAudio:
    """Distinctive fake source used by adapter tests."""

    def __init__(self, url: str) -> None:
        self.url = url


class FakeVoiceClient:
    def __init__(self) -> None:
        self.after = None
        self.source = None
        self._is_playing = False
        self._is_paused = False

    def play(self, source, *, after) -> None:
        self.source = source
        self.after = after
        self._is_playing = True

    def is_playing(self) -> bool:
        return self._is_playing

    def is_paused(self) -> bool:
        return self._is_paused

    def stop(self) -> None:
        self._is_playing = False


@pytest.mark.asyncio
async def test_discord_ffmpeg_backend_registers_and_dispatches_track_end_hook(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = DiscordFFmpegPlaybackBackend(guild_id=77)
    fake_source = FakeFFmpegPCMAudio("https://cdn.example.com/track.opus")
    voice_client = FakeVoiceClient()
    observed: list[tuple[object, object, Exception | None]] = []

    def fake_build_source(url: str) -> object:
        assert url == "https://cdn.example.com/track.opus"
        return fake_source

    async def fake_cleanup_source(source: object) -> None:
        assert source is fake_source

    backend._loop = asyncio.get_running_loop()
    monkeypatch.setattr(backend, "_build_source", fake_build_source)
    monkeypatch.setattr(backend, "_cleanup_source", fake_cleanup_source)

    async def hook(vc, source, error):
        observed.append((vc, source, error))

    backend.add_track_end_hook(hook)

    source = await backend.play_track(voice_client, "https://cdn.example.com/track.opus")
    assert source is fake_source
    assert voice_client.after is not None

    voice_client.after(None)
    await asyncio.sleep(0)

    assert observed == [(voice_client, fake_source, None)]


@pytest.mark.asyncio
async def test_lavalink_backend_delegates_to_compatibility_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeFallbackBackend:
        def __init__(self, guild_id: int) -> None:
            self.guild_id = guild_id
            self.played: list[str] = []
            self.hooks = []

        async def connect(self, channel):
            return "connected"

        async def disconnect(self, voice_client) -> None:
            return None

        async def play_track(self, voice_client, url: str) -> object:
            self.played.append(url)
            return "fallback-source"

        async def stop(self, voice_client) -> None:
            return None

        async def skip(self, voice_client) -> None:
            return None

        def is_playing(self, voice_client) -> bool:
            return False

        def add_track_end_hook(self, hook) -> None:
            self.hooks.append(hook)

    monkeypatch.setattr("jukebotx_bot.voice.backends.lavalink.DiscordFFmpegPlaybackBackend", FakeFallbackBackend)
    backend = LavalinkPlaybackBackend(guild_id=88)

    def dummy_hook(*args, **kwargs):
        return None

    backend.add_track_end_hook(dummy_hook)
    source = await backend.play_track(FakeVoiceClient(), "https://cdn.example.com/track.mp3")

    assert source == "fallback-source"
    assert backend._fallback_backend.played == ["https://cdn.example.com/track.mp3"]
    assert backend._fallback_backend.hooks == [dummy_hook]


def test_discord_ffmpeg_backend_rejects_non_audio_page_url() -> None:
    backend = DiscordFFmpegPlaybackBackend(guild_id=99)

    with pytest.raises(ValueError):
        backend._assert_audio_url("https://suno.com/song/abc123")
