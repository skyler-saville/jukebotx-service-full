from __future__ import annotations

import asyncio
import inspect
import logging
import subprocess
import threading

import discord

from jukebotx_bot.voice.backends.base import PlaybackBackend, TrackEndHook


logger = logging.getLogger(__name__)


class DiscordFFmpegPlaybackBackend(PlaybackBackend):
    def __init__(self, guild_id: int) -> None:
        self.guild_id = guild_id
        self._current_source: discord.FFmpegOpusAudio | None = None
        self._stderr_thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._track_end_hooks: list[TrackEndHook] = []

    async def connect(self, channel: discord.VocalGuildChannel) -> discord.VoiceClient:
        return await channel.connect()

    async def disconnect(self, voice_client: discord.VoiceClient) -> None:
        await self._cleanup_current_source()
        if voice_client.is_connected():
            await voice_client.disconnect()

    async def play_track(self, voice_client: discord.VoiceClient, url: str) -> object:
        source = self._build_source(url)
        self._current_source = source

        if self._loop is None:
            self._loop = asyncio.get_running_loop()

        def _after_playback(error: Exception | None, *, current_source=source) -> None:
            if self._loop is None:
                return
            asyncio.run_coroutine_threadsafe(
                self._dispatch_track_end(voice_client, current_source, error),
                self._loop,
            )

        voice_client.play(source, after=_after_playback)
        return source

    async def stop(self, voice_client: discord.VoiceClient) -> None:
        if voice_client.is_playing() or voice_client.is_paused():
            voice_client.stop()
        await self._cleanup_current_source()

    async def skip(self, voice_client: discord.VoiceClient) -> None:
        await self.stop(voice_client)

    def is_playing(self, voice_client: discord.VoiceClient) -> bool:
        return voice_client.is_playing() or voice_client.is_paused()

    def add_track_end_hook(self, hook: TrackEndHook) -> None:
        self._track_end_hooks.append(hook)

    async def _dispatch_track_end(
        self,
        voice_client: discord.VoiceClient,
        source: discord.FFmpegOpusAudio,
        error: Exception | None,
    ) -> None:
        if self._current_source is source:
            await self._cleanup_source(source)
            self._current_source = None

        for hook in self._track_end_hooks:
            result = hook(voice_client, source, error)
            if inspect.isawaitable(result):
                await result

    def _build_source(self, url: str) -> discord.FFmpegOpusAudio:
        self._assert_audio_url(url)
        source = discord.FFmpegOpusAudio(
            url,
            before_options="-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
            options="-vn",
            stderr=subprocess.PIPE,
        )
        self._start_ffmpeg_logger(source)
        return source

    def _assert_audio_url(self, url: str) -> None:
        lowered = url.lower()
        stripped = lowered.split("?", 1)[0]
        if not lowered.startswith("http"):
            raise ValueError(f"Audio URL must be http(s): {url}")
        if "suno.com/song/" in lowered or "suno.com/s/" in lowered:
            raise ValueError(f"Refusing to pass Suno page URL to ffmpeg: {url}")
        if not (stripped.endswith(".mp3") or stripped.endswith(".opus") or stripped.endswith("/opus") or "cdn" in lowered):
            raise ValueError(f"Refusing to pass non-audio URL to ffmpeg: {url}")

    def _start_ffmpeg_logger(self, source: discord.FFmpegOpusAudio) -> None:
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

    async def _cleanup_current_source(self) -> None:
        source = self._current_source
        if source is None:
            return
        await self._cleanup_source(source)
        self._current_source = None

    async def _cleanup_source(self, source: discord.FFmpegOpusAudio) -> None:
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
