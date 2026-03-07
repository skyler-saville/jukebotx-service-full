from __future__ import annotations

import inspect
import logging
from typing import TYPE_CHECKING, Any

import discord

from jukebotx_bot.voice.backends.base import PlaybackBackend, TrackEndHook

if TYPE_CHECKING:
    import lavalink


logger = logging.getLogger(__name__)


class LavalinkPlaybackBackend(PlaybackBackend):
    _client: "lavalink.Client | None" = None
    _event_hook_registered = False
    _backends_by_guild: dict[int, "LavalinkPlaybackBackend"] = {}

    def __init__(self, guild_id: int) -> None:
        self.guild_id = guild_id
        self._track_end_hooks: list[TrackEndHook] = []
        self._voice_client: discord.VoiceClient | None = None
        type(self)._backends_by_guild[guild_id] = self

    @classmethod
    def configure_client(cls, client: "lavalink.Client | None") -> None:
        cls._client = client
        cls._event_hook_registered = False

    @classmethod
    async def _dispatch_track_end(cls, event: object) -> None:
        guild_id = getattr(event, "guild_id", None)
        if guild_id is None and getattr(event, "player", None) is not None:
            guild_id = getattr(event.player, "guild_id", None)
        if guild_id is None:
            return

        backend = cls._backends_by_guild.get(int(guild_id))
        if backend is None or backend._voice_client is None:
            return

        reason = str(getattr(event, "reason", "")).upper()
        if reason in {"REPLACED", "STOPPED"}:
            return

        track = getattr(event, "track", None)
        if track is None and getattr(event, "player", None) is not None:
            track = getattr(event.player, "current", None)

        exception = getattr(event, "exception", None)
        if exception is not None and not isinstance(exception, Exception):
            exception = Exception(str(exception))

        for hook in list(backend._track_end_hooks):
            result = hook(backend._voice_client, track, exception)
            if inspect.isawaitable(result):
                await result

    @classmethod
    def _register_event_hook_if_needed(cls) -> None:
        if cls._client is None or cls._event_hook_registered:
            return

        async def _handler(event: object) -> None:
            event_name = type(event).__name__.lower()
            if "trackend" in event_name or "track_end" in event_name:
                await cls._dispatch_track_end(event)

        client = cls._client
        assert client is not None
        if hasattr(client, "add_event_hook"):
            client.add_event_hook(_handler)
        elif hasattr(client, "add_event_hooks"):
            client.add_event_hooks(_handler)
        else:
            logger.warning("Lavalink client does not support event hooks; track-end callbacks disabled.")
            return
        cls._event_hook_registered = True

    @classmethod
    def _require_client(cls) -> "lavalink.Client":
        if cls._client is None:
            raise RuntimeError("LavalinkPlaybackBackend is not configured with a lavalink client")
        cls._register_event_hook_if_needed()
        return cls._client

    @staticmethod
    async def _maybe_await(value: object) -> object:
        if inspect.isawaitable(value):
            return await value
        return value

    def _get_existing_player(self) -> Any | None:
        client = self._require_client()
        player_manager = getattr(client, "player_manager", None)
        if player_manager is not None and hasattr(player_manager, "get"):
            return player_manager.get(self.guild_id)
        if hasattr(client, "get_player"):
            return client.get_player(self.guild_id)
        return None

    async def _get_or_create_player(self) -> Any:
        client = self._require_client()
        player = self._get_existing_player()
        if player is not None:
            return player

        player_manager = getattr(client, "player_manager", None)
        if player_manager is not None:
            if hasattr(player_manager, "create"):
                return await self._maybe_await(player_manager.create(self.guild_id))
            if hasattr(player_manager, "create_player"):
                return await self._maybe_await(player_manager.create_player(self.guild_id))
        if hasattr(client, "create_player"):
            return await self._maybe_await(client.create_player(self.guild_id))

        raise RuntimeError("Lavalink client does not expose a compatible player creation API")

    async def _resolve_track(self, url: str) -> object:
        client = self._require_client()

        if hasattr(client, "get_tracks"):
            result = await self._maybe_await(client.get_tracks(url))
        elif hasattr(client, "load_tracks"):
            result = await self._maybe_await(client.load_tracks(url))
        else:
            raise RuntimeError("Lavalink client does not expose a track loading API")

        tracks: list[object]
        if isinstance(result, list):
            tracks = result
        elif hasattr(result, "tracks"):
            tracks = list(result.tracks)
        elif isinstance(result, dict) and isinstance(result.get("tracks"), list):
            tracks = result["tracks"]
        else:
            tracks = [result]

        if not tracks:
            raise ValueError(f"No Lavalink tracks resolved for URL: {url}")
        return tracks[0]

    async def connect(self, channel: discord.VocalGuildChannel) -> discord.VoiceClient:
        self._require_client()

        voice_client = channel.guild.voice_client
        if voice_client is None:
            voice_client = await channel.connect()
        elif voice_client.channel != channel:
            await voice_client.move_to(channel)

        self._voice_client = voice_client
        await self._get_or_create_player()
        return voice_client

    async def disconnect(self, voice_client: discord.VoiceClient) -> None:
        client = self._require_client()

        player = self._get_existing_player()
        if player is not None and hasattr(player, "stop"):
            await self._maybe_await(player.stop())

        if hasattr(client, "player_manager") and hasattr(client.player_manager, "destroy"):
            await self._maybe_await(client.player_manager.destroy(self.guild_id))
        elif hasattr(client, "destroy_player"):
            await self._maybe_await(client.destroy_player(self.guild_id))

        self._voice_client = None
        await voice_client.disconnect(force=True)

    async def play_track(self, voice_client: discord.VoiceClient, url: str) -> object:
        self._voice_client = voice_client
        player = await self._get_or_create_player()
        track = await self._resolve_track(url)

        if not hasattr(player, "play"):
            raise RuntimeError("Lavalink player does not support play()")

        await self._maybe_await(player.play(track))
        return track

    async def stop(self, voice_client: discord.VoiceClient) -> None:
        del voice_client
        player = self._get_existing_player()
        if player is not None and hasattr(player, "stop"):
            await self._maybe_await(player.stop())

    async def skip(self, voice_client: discord.VoiceClient) -> None:
        await self.stop(voice_client)

    def is_playing(self, voice_client: discord.VoiceClient) -> bool:
        del voice_client

        try:
            player = self._get_existing_player()
        except RuntimeError:
            return False
        if player is None:
            return False

        is_playing = getattr(player, "is_playing", None)
        if callable(is_playing):
            return bool(is_playing())
        if isinstance(is_playing, bool):
            return is_playing
        if hasattr(player, "playing"):
            return bool(player.playing)
        return getattr(player, "current", None) is not None

    def add_track_end_hook(self, hook: TrackEndHook) -> None:
        self._track_end_hooks.append(hook)
