from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import logging

import discord

from jukebotx_bot.discord.audio import AudioControllerManager
from jukebotx_bot.discord.session import SessionManager, Track


logger = logging.getLogger(__name__)


class JoinResult(str, Enum):
    ALREADY_IN_CHANNEL = "already_in_channel"
    MOVED = "moved"
    JOINED = "joined"


@dataclass(frozen=True)
class JoinOutcome:
    result: JoinResult
    channel_name: str


class VoiceOrchestrationService:
    def __init__(self, *, session_manager: SessionManager, audio_manager: AudioControllerManager) -> None:
        self._session_manager = session_manager
        self._audio_manager = audio_manager

    async def join(self, guild: discord.Guild, channel: discord.VocalGuildChannel) -> JoinOutcome:
        session = self._session_manager.for_guild(guild.id)
        audio = self._audio_manager.for_guild(guild.id, session)
        voice_client = guild.voice_client

        if voice_client is not None:
            if voice_client.channel and voice_client.channel.id == channel.id:
                return JoinOutcome(JoinResult.ALREADY_IN_CHANNEL, channel.name)

            if voice_client.is_connected():
                await voice_client.move_to(channel)
                return JoinOutcome(JoinResult.MOVED, channel.name)

            try:
                await audio.disconnect(voice_client)
            except Exception:
                logger.warning(
                    "Failed to force-disconnect stale voice client for guild %s",
                    guild.id,
                    exc_info=True,
                )

        await audio.connect(channel)
        return JoinOutcome(JoinResult.JOINED, channel.name)

    async def leave(self, guild: discord.Guild) -> None:
        voice_client = guild.voice_client
        if voice_client is None:
            return

        session = self._session_manager.for_guild(guild.id)
        audio = self._audio_manager.for_guild(guild.id, session)
        await audio.stop(voice_client)
        await audio.disconnect(voice_client)

    async def play_next(self, guild: discord.Guild) -> Track | None:
        voice_client = guild.voice_client
        if voice_client is None:
            return None

        session = self._session_manager.for_guild(guild.id)
        audio = self._audio_manager.for_guild(guild.id, session)
        return await audio.play_next(voice_client)

    async def skip(self, guild: discord.Guild) -> Track | None:
        voice_client = guild.voice_client
        if voice_client is None:
            return None

        session = self._session_manager.for_guild(guild.id)
        audio = self._audio_manager.for_guild(guild.id, session)
        return await audio.skip(voice_client)

    async def stop(self, guild: discord.Guild) -> bool:
        voice_client = guild.voice_client
        if voice_client is None:
            return False

        session = self._session_manager.for_guild(guild.id)
        audio = self._audio_manager.for_guild(guild.id, session)
        await audio.stop(voice_client)
        return True
