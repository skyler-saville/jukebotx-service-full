# ruff: noqa: E402
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
    await asyncio.sleep(0.05)

    assert observed == [(voice_client, fake_source, None)]


class FakeLavalinkPlayer:
    def __init__(self, guild_id: int) -> None:
        self.guild_id = guild_id
        self.is_connected = True
        self.playing = False
        self.current = None

    async def play(self, track: object) -> None:
        self.current = track
        self.playing = True

    async def stop(self) -> None:
        self.playing = False


class FakePlayerManager:
    def __init__(self) -> None:
        self.players: dict[int, FakeLavalinkPlayer] = {}

    def get(self, guild_id: int):
        return self.players.get(guild_id)

    def create(self, guild_id: int):
        player = FakeLavalinkPlayer(guild_id)
        self.players[guild_id] = player
        return player

    async def destroy(self, guild_id: int) -> None:
        self.players.pop(guild_id, None)


class FakeLavalinkClient:
    def __init__(self) -> None:
        self.player_manager = FakePlayerManager()
        self._hooks = []

    def add_event_hook(self, hook):
        self._hooks.append(hook)

    async def get_tracks(self, url: str):
        return [{"identifier": url}]


class FakeChannel:
    def __init__(self, guild) -> None:
        self.guild = guild
        self.id = 12345


class FakeGuild:
    def __init__(self) -> None:
        self.voice_client = None


class FakeDiscordVoiceClient:
    def __init__(self, guild: FakeGuild, channel: FakeChannel) -> None:
        self.guild = guild
        self.channel = channel
        self.disconnected = False

    async def move_to(self, channel: FakeChannel) -> None:
        self.channel = channel

    async def disconnect(self, *, force: bool) -> None:
        assert force is True
        self.disconnected = True
        self.guild.voice_client = None


@pytest.mark.asyncio
async def test_lavalink_backend_uses_lavalink_player_and_dispatches_end_hooks() -> None:
    LavalinkPlaybackBackend._backends_by_guild.clear()
    client = FakeLavalinkClient()
    LavalinkPlaybackBackend.configure_client(client)

    backend = LavalinkPlaybackBackend(guild_id=88)
    guild = FakeGuild()
    channel = FakeChannel(guild)

    async def connect():
        vc = FakeDiscordVoiceClient(guild, channel)
        guild.voice_client = vc
        return vc

    channel.connect = connect
    voice_client = await backend.connect(channel)

    observed = []

    async def hook(vc, source, error):
        observed.append((vc, source, error))

    backend.add_track_end_hook(hook)

    source = await backend.play_track(voice_client, "https://cdn.example.com/track.mp3")
    assert source == {"identifier": "https://cdn.example.com/track.mp3"}
    assert backend.is_playing(voice_client) is True

    event = type(
        "TrackEndEvent",
        (),
        {
            "guild_id": 88,
            "reason": "FINISHED",
            "track": source,
            "exception": None,
        },
    )()
    await client._hooks[0](event)

    assert observed == [(voice_client, source, None)]

    await backend.stop(voice_client)
    assert backend.is_playing(voice_client) is False

    await backend.disconnect(voice_client)
    assert voice_client.disconnected is True
    assert client.player_manager.get(88) is None


def test_discord_ffmpeg_backend_rejects_non_audio_page_url() -> None:
    backend = DiscordFFmpegPlaybackBackend(guild_id=99)

    with pytest.raises(ValueError):
        backend._assert_audio_url("https://suno.com/song/abc123")
