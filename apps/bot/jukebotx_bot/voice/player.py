from __future__ import annotations

from dataclasses import dataclass

from jukebotx_bot.discord.audio import AudioControllerManager


@dataclass
class VoicePlayer:
    """Thin playback abstraction used by command and event handlers."""

    audio_manager: AudioControllerManager

    async def skip(self, *, guild_id: int, source: str = "command") -> bool:
        controller = self.audio_manager.for_guild(guild_id)
        return await controller.skip(source=source)

    async def pause(self, *, guild_id: int, source: str = "command") -> bool:
        controller = self.audio_manager.for_guild(guild_id)
        return await controller.pause(source=source)

    async def resume(self, *, guild_id: int, source: str = "command") -> bool:
        controller = self.audio_manager.for_guild(guild_id)
        return await controller.resume(source=source)

    async def stop(self, *, guild_id: int, source: str = "command") -> bool:
        controller = self.audio_manager.for_guild(guild_id)
        return await controller.stop(source=source)
