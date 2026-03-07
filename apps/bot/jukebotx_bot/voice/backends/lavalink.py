from __future__ import annotations

import logging

import discord

from jukebotx_bot.voice.backends.base import PlaybackBackend, TrackEndHook
from jukebotx_bot.voice.backends.discord_ffmpeg import DiscordFFmpegPlaybackBackend


logger = logging.getLogger(__name__)


class LavalinkPlaybackBackend(PlaybackBackend):
    """
    Compatibility Lavalink backend.

    This backend allows routing by config during rollout while delegating playback
    to the existing Discord+FFmpeg implementation until full Lavalink wiring is enabled.
    """

    def __init__(self, guild_id: int) -> None:
        self.guild_id = guild_id
        self._fallback_backend = DiscordFFmpegPlaybackBackend(guild_id)
        logger.warning(
            "VOICE_BACKEND=lavalink is enabled for guild=%s; using FFmpeg compatibility backend.",
            guild_id,
        )

    async def connect(self, channel: discord.VocalGuildChannel) -> discord.VoiceClient:
        return await self._fallback_backend.connect(channel)

    async def disconnect(self, voice_client: discord.VoiceClient) -> None:
        await self._fallback_backend.disconnect(voice_client)

    async def play_track(self, voice_client: discord.VoiceClient, url: str) -> object:
        return await self._fallback_backend.play_track(voice_client, url)

    async def stop(self, voice_client: discord.VoiceClient) -> None:
        await self._fallback_backend.stop(voice_client)

    async def skip(self, voice_client: discord.VoiceClient) -> None:
        await self._fallback_backend.skip(voice_client)

    def is_playing(self, voice_client: discord.VoiceClient) -> bool:
        return self._fallback_backend.is_playing(voice_client)

    def add_track_end_hook(self, hook: TrackEndHook) -> None:
        self._fallback_backend.add_track_end_hook(hook)
