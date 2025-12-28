from __future__ import annotations

import asyncio
import logging
import subprocess
import threading
from typing import Optional

import discord

from jukebotx_bot.discord.session import SessionState, Track


logger = logging.getLogger(__name__)


class GuildAudioController:
    def __init__(self, guild_id: int, session: SessionState) -> None:
        self.guild_id = guild_id
        self.session = session
        self._lock = asyncio.Lock()
        self._current_source: Optional[discord.FFmpegPCMAudio] = None
        self._stderr_thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    async def play_next(self, voice_client: discord.VoiceClient) -> Track | None:
        async with self._lock:
            if voice_client.is_playing() or voice_client.is_paused():
                return None

            track = self.session.start_next_track()
            if track is None:
                return None

            try:
                source = self._build_source(track.audio_url)
            except ValueError as exc:
                logger.error("Refusing to play invalid audio URL for guild %s: %s", self.guild_id, exc)
                self.session.stop_playback()
                return None
            self._current_source = source

            if self._loop is None:
                self._loop = asyncio.get_running_loop()

            def _after_playback(error: Exception | None, *, current_source=source) -> None:
                if self._loop is None:
                    return
                asyncio.run_coroutine_threadsafe(
                    self._on_track_end(voice_client, current_source, error),
                    self._loop,
                )

            voice_client.play(source, after=_after_playback)
            return track

    async def stop(self, voice_client: discord.VoiceClient) -> None:
        async with self._lock:
            if voice_client.is_playing() or voice_client.is_paused():
                voice_client.stop()
            await self._cleanup_ffmpeg()
            self.session.stop_playback()

    async def skip(self, voice_client: discord.VoiceClient) -> Track | None:
        await self.stop(voice_client)
        return await self.play_next(voice_client)

    async def _on_track_end(
        self,
        voice_client: discord.VoiceClient,
        source: discord.FFmpegPCMAudio,
        error: Exception | None,
    ) -> None:
        if error is not None:
            logger.warning("Playback error in guild %s: %s", self.guild_id, error)

        async with self._lock:
            if self._current_source is not source:
                return
            await self._cleanup_ffmpeg()
            self.session.stop_playback()

        if (self.session.autoplay_enabled or self.session.dj_enabled) and self.session.queue:
            await self.play_next(voice_client)

    def _build_source(self, url: str) -> discord.FFmpegPCMAudio:
        self._assert_audio_url(url)
        source = discord.FFmpegPCMAudio(
            url,
            before_options="-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
            options="-vn",
            stderr=subprocess.PIPE,
        )
        self._start_ffmpeg_logger(source)
        return source

    def _assert_audio_url(self, url: str) -> None:
        lowered = url.lower()
        if not lowered.startswith("http"):
            raise ValueError(f"Audio URL must be http(s): {url}")
        if "suno.com/song/" in lowered or "suno.com/s/" in lowered:
            raise ValueError(f"Refusing to pass Suno page URL to ffmpeg: {url}")
        if not (lowered.endswith(".mp3") or "cdn" in lowered):
            raise ValueError(f"Refusing to pass non-audio URL to ffmpeg: {url}")

    def _start_ffmpeg_logger(self, source: discord.FFmpegPCMAudio) -> None:
        process = getattr(source, "process", None)
        if process is None or process.stderr is None:
            return

        def _read_stderr() -> None:
            for raw_line in iter(process.stderr.readline, b""):
                if not raw_line:
                    break
                line = raw_line.decode(errors="replace").rstrip()
                if line:
                    logger.warning("FFmpeg stderr [guild=%s]: %s", self.guild_id, line)

        self._stderr_thread = threading.Thread(
            target=_read_stderr,
            name=f"ffmpeg-stderr-{self.guild_id}",
            daemon=True,
        )
        self._stderr_thread.start()

    async def _cleanup_ffmpeg(self) -> None:
        source = self._current_source
        if source is None:
            return

        process = getattr(source, "process", None)
        if process is not None:
            try:
                if process.stdin is not None:
                    process.stdin.close()
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("Failed to close ffmpeg stdin: %s", exc)

            try:
                process.terminate()
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("Failed to terminate ffmpeg process: %s", exc)

        try:
            source.cleanup()
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Failed to cleanup ffmpeg source: %s", exc)

        self._current_source = None


class AudioControllerManager:
    def __init__(self) -> None:
        self._controllers: dict[int, GuildAudioController] = {}

    def for_guild(self, guild_id: int, session: SessionState) -> GuildAudioController:
        if guild_id not in self._controllers:
            self._controllers[guild_id] = GuildAudioController(guild_id, session)
        return self._controllers[guild_id]
