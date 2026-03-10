from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import discord

TrackEndHook = Callable[["discord.VoiceClient", object, Exception | None], Awaitable[None] | None]


class PlaybackBackend(ABC):
    @abstractmethod
    async def connect(self, channel: "discord.VocalGuildChannel") -> "discord.VoiceClient":
        raise NotImplementedError

    @abstractmethod
    async def disconnect(self, voice_client: "discord.VoiceClient") -> None:
        raise NotImplementedError

    @abstractmethod
    async def play_track(self, voice_client: "discord.VoiceClient", url: str) -> object:
        raise NotImplementedError

    @abstractmethod
    async def stop(self, voice_client: "discord.VoiceClient") -> None:
        raise NotImplementedError

    @abstractmethod
    async def skip(self, voice_client: "discord.VoiceClient") -> None:
        raise NotImplementedError

    @abstractmethod
    def is_playing(self, voice_client: "discord.VoiceClient") -> bool:
        raise NotImplementedError

    @abstractmethod
    def add_track_end_hook(self, hook: TrackEndHook) -> None:
        raise NotImplementedError

    def prefer_source_audio_url(self) -> bool:
        """Whether playback should prefer the original source audio URL over opus cache URLs."""
        return False
