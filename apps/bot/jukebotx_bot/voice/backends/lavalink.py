from __future__ import annotations

import asyncio
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
        self._voice_connect_lock = asyncio.Lock()
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
            elif "trackstart" in event_name or "track_start" in event_name:
                logger.info("Lavalink track start event: guild=%s", getattr(event, "guild_id", None))
            elif "trackexception" in event_name or "track_exception" in event_name:
                logger.error(
                    "Lavalink track exception event: guild=%s exception=%s",
                    getattr(event, "guild_id", None),
                    getattr(event, "exception", None),
                )
            elif "trackstuck" in event_name or "track_stuck" in event_name:
                logger.warning(
                    "Lavalink track stuck event: guild=%s threshold_ms=%s",
                    getattr(event, "guild_id", None),
                    getattr(event, "threshold_ms", None),
                )

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
        # Create the Lavalink player before joining voice so initial VOICE_* gateway
        # updates can be attached to an existing player.
        player = await self._get_or_create_player()

        voice_client = channel.guild.voice_client
        if voice_client is None:
            voice_client_cls = self._resolve_voice_client_cls()
            if voice_client_cls is not None:
                voice_client = await channel.connect(cls=voice_client_cls)
            else:
                voice_client = await channel.connect()
        elif voice_client.channel != channel:
            await voice_client.move_to(channel)

        self._voice_client = voice_client
        await self._dispatch_voice_state_from_voice_client(player, voice_client, channel.id)
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
        resolved_voice_client = await self._ensure_player_voice_connected(player, voice_client)
        self._voice_client = resolved_voice_client

        track = await self._resolve_track(url)

        if not hasattr(player, "play"):
            raise RuntimeError("Lavalink player does not support play()")

        await self._maybe_await(player.play(track))
        # Explicitly enforce play-state. Some sessions can remain paused across reconnects.
        if hasattr(player, "set_pause"):
            await self._maybe_await(player.set_pause(False))
        if hasattr(player, "set_volume"):
            await self._maybe_await(player.set_volume(100))
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

    def prefer_source_audio_url(self) -> bool:
        return True

    async def _dispatch_voice_state_from_voice_client(
        self,
        player: Any,
        voice_client: discord.VoiceClient,
        channel_id: int,
    ) -> None:
        voice_payload: dict[str, str] | None = None
        for _ in range(20):
            session_id = getattr(voice_client, "session_id", None)
            endpoint = getattr(voice_client, "endpoint", None)
            token = getattr(voice_client, "token", None)
            if session_id and endpoint and token:
                voice_payload = {
                    "sessionId": str(session_id),
                    "channelId": str(channel_id),
                    "endpoint": str(endpoint),
                    "token": str(token),
                }
                break
            await asyncio.sleep(0.1)

        if voice_payload is None:
            logger.warning(
                "Could not extract full Discord voice state for guild %s; Lavalink may stay silent.",
                self.guild_id,
            )
            return

        node = getattr(player, "node", None)
        if node is None or not hasattr(node, "update_player"):
            logger.warning("Lavalink player node missing update_player for guild %s.", self.guild_id)
            return

        await self._maybe_await(node.update_player(guild_id=self.guild_id, voice_state=voice_payload))

    async def _ensure_player_voice_connected(
        self,
        player: Any,
        voice_client: discord.VoiceClient,
    ) -> discord.VoiceClient:
        if bool(getattr(player, "is_connected", False)):
            return voice_client

        async with self._voice_connect_lock:
            # Another coroutine may have connected the player while we were waiting.
            if bool(getattr(player, "is_connected", False)):
                return self._coalesce_voice_client(voice_client)

            for attempt in range(1, 5):
                active_voice_client = self._coalesce_voice_client(voice_client)
                channel = getattr(active_voice_client, "channel", None)
                if channel is not None and hasattr(channel, "id"):
                    await self._dispatch_voice_state_from_voice_client(
                        player,
                        active_voice_client,
                        int(channel.id),
                    )
                else:
                    logger.warning(
                        "Missing voice channel on attempt %s for guild %s while syncing Lavalink voice state.",
                        attempt,
                        self.guild_id,
                    )

                for _ in range(20):
                    if bool(getattr(player, "is_connected", False)):
                        return active_voice_client
                    await asyncio.sleep(0.1)

                if attempt < 4:
                    backoff_seconds = 0.25 * attempt
                    logger.warning(
                        "Lavalink player still not connected for guild %s after attempt %s; retrying in %.2fs.",
                        self.guild_id,
                        attempt,
                        backoff_seconds,
                    )
                    await asyncio.sleep(backoff_seconds)

            raise RuntimeError(
                f"Lavalink player is not voice-connected for guild {self.guild_id}; "
                "voice state dispatch did not establish a connection."
            )

    def _coalesce_voice_client(self, fallback: discord.VoiceClient) -> discord.VoiceClient:
        cached = self._voice_client
        if cached is not None:
            return cached
        guild = getattr(fallback, "guild", None)
        guild_voice_client = getattr(guild, "voice_client", None)
        if guild_voice_client is not None:
            return guild_voice_client
        return fallback

    @staticmethod
    def _resolve_voice_client_cls() -> type[discord.VoiceClient] | None:
        try:
            import lavalink as lavalink_module
        except Exception:
            return None

        voice_client_cls = getattr(lavalink_module, "LavalinkVoiceClient", None)
        if voice_client_cls is None:
            voice_client_cls = getattr(lavalink_module, "DiscordVoiceClient", None)
        return voice_client_cls
