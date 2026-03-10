# ruff: noqa: E402
from pathlib import Path
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

from jukebotx_bot.voice.backends.lavalink import LavalinkPlaybackBackend


class FakeLavalinkPlayer:
    def __init__(self, guild_id: int) -> None:
        self.guild_id = guild_id
        self.channel_id = None
        self.playing = False
        self.current = None
        self._voice_state = {}
        self.node = FakeNode()

    @property
    def is_connected(self) -> bool:
        return self.channel_id is not None

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


class FakeNode:
    def __init__(self) -> None:
        self.voice_updates: list[tuple[int, dict[str, str]]] = []

    async def update_player(self, *, guild_id: int, voice_state: dict[str, str]) -> None:
        self.voice_updates.append((guild_id, voice_state))


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
        self.session_id = "discord-session"
        self.endpoint = "voice.example.test"
        self.token = "voice-token"

    async def move_to(self, channel: FakeChannel) -> None:
        self.channel = channel

    async def disconnect(self, *, force: bool) -> None:
        assert force is True
        self.disconnected = True
        self.guild.voice_client = None


class FakeLegacyVoiceClient(FakeDiscordVoiceClient):
    pass


@pytest.mark.asyncio
async def test_lavalink_backend_uses_lavalink_player_and_dispatches_end_hooks() -> None:
    LavalinkPlaybackBackend._backends_by_guild.clear()
    client = FakeLavalinkClient()
    LavalinkPlaybackBackend.configure_client(client)

    backend = LavalinkPlaybackBackend(guild_id=88)
    guild = FakeGuild()
    channel = FakeChannel(guild)

    async def connect(*, cls=None):
        assert cls is not None
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
            "track": {"identifier": source["identifier"]},
            "exception": None,
        },
    )()
    await client._hooks[0](event)

    assert observed == [(voice_client, {"identifier": source["identifier"]}, None)]

    await backend.stop(voice_client)
    assert backend.is_playing(voice_client) is False

    await backend.disconnect(voice_client)
    assert voice_client.disconnected is True
    assert client.player_manager.get(88) is None


@pytest.mark.asyncio
async def test_lavalink_backend_marks_player_connected_after_manual_voice_dispatch() -> None:
    LavalinkPlaybackBackend._backends_by_guild.clear()
    client = FakeLavalinkClient()
    LavalinkPlaybackBackend.configure_client(client)

    backend = LavalinkPlaybackBackend(guild_id=144)
    guild = FakeGuild()
    channel = FakeChannel(guild)
    voice_client = FakeDiscordVoiceClient(guild, channel)
    player = await backend._get_or_create_player()

    assert player.is_connected is False

    await backend._dispatch_voice_state_from_voice_client(player, voice_client, channel.id)

    assert player.is_connected is True
    assert player.channel_id == channel.id
    assert player._voice_state == {
        "sessionId": "discord-session",
        "channelId": str(channel.id),
        "endpoint": "voice.example.test",
        "token": "voice-token",
    }
    assert player.node.voice_updates == [
        (
            144,
            {
                "sessionId": "discord-session",
                "channelId": str(channel.id),
                "endpoint": "voice.example.test",
                "token": "voice-token",
            },
        )
    ]


@pytest.mark.asyncio
async def test_lavalink_backend_drops_stale_non_lavalink_voice_client_before_connecting() -> None:
    LavalinkPlaybackBackend._backends_by_guild.clear()
    client = FakeLavalinkClient()
    LavalinkPlaybackBackend.configure_client(client)

    backend = LavalinkPlaybackBackend(guild_id=177)
    guild = FakeGuild()
    channel = FakeChannel(guild)
    stale_voice_client = FakeLegacyVoiceClient(guild, channel)
    guild.voice_client = stale_voice_client

    async def connect(*, cls=None):
        assert cls is not None
        vc = FakeDiscordVoiceClient(guild, channel)
        guild.voice_client = vc
        return vc

    channel.connect = connect

    voice_client = await backend.connect(channel)

    assert stale_voice_client.disconnected is True
    assert voice_client is guild.voice_client

