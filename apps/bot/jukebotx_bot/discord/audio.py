from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
from typing import Optional

import httpx

import discord

from jukebotx_bot.discord.now_playing import build_now_playing_embed
from jukebotx_bot.discord.session import SessionState, Track
from jukebotx_bot.voice.backends.base import PlaybackBackend
from jukebotx_bot.voice.backends.lavalink import LavalinkPlaybackBackend
from jukebotx_infra.suno.client import HttpxSunoClient, SunoScrapeError


logger = logging.getLogger(__name__)

_OPUS_READY_TIMEOUT_SECONDS = float(os.getenv("OPUS_READY_TIMEOUT_SECONDS", "30"))
_OPUS_READY_POLL_SECONDS = float(os.getenv("OPUS_READY_POLL_SECONDS", "2"))
_FFPROBE_TIMEOUT_SECONDS = float(os.getenv("FFPROBE_TIMEOUT_SECONDS", "10"))
_FFPROBE_PATH = os.getenv("FFPROBE_PATH", "ffprobe")


class GuildAudioController:
    def __init__(self, guild_id: int, session: SessionState, *, backend: PlaybackBackend | None = None) -> None:
        self.guild_id = guild_id
        self.session = session
        self._lock = asyncio.Lock()
        self._backend = backend or LavalinkPlaybackBackend(guild_id)
        self._backend.add_track_end_hook(self._on_track_end)
        self._current_source: Optional[object] = None
        self._suno_client = HttpxSunoClient()

    async def play_next(self, voice_client: discord.VoiceClient) -> Track | None:
        async with self._lock:
            if self._backend.is_playing(voice_client):
                return None

            track = self.session.start_next_track()
            if track is None:
                return None

            try:
                if not self._backend.prefer_source_audio_url():
                    await self._wait_for_opus_ready(track)
                playback_url = await self._resolve_playback_url(track)
                source = await self._backend.play_track(voice_client, playback_url)
            except ValueError as exc:
                logger.error("Refusing to play invalid audio URL for guild %s: %s", self.guild_id, exc)
                self.session.rollback_started_track(track)
                return None
            except Exception:
                self.session.rollback_started_track(track)
                raise
            self._current_source = source
            if track.duration_seconds is None:
                asyncio.create_task(self._backfill_track_duration(track, playback_url))
            return track

    async def connect(self, channel: discord.VocalGuildChannel) -> discord.VoiceClient:
        return await self._backend.connect(channel)

    async def disconnect(self, voice_client: discord.VoiceClient) -> None:
        await self._backend.disconnect(voice_client)

    async def stop(self, voice_client: discord.VoiceClient) -> None:
        async with self._lock:
            await self._backend.stop(voice_client)
            self._current_source = None
            self.session.stop_playback()

    async def skip(self, voice_client: discord.VoiceClient) -> Track | None:
        await self.stop(voice_client)
        return await self.play_next(voice_client)

    async def _on_track_end(
        self,
        voice_client: discord.VoiceClient,
        source: object,
        error: Exception | None,
    ) -> None:
        if error is not None:
            logger.warning("Playback error in guild %s: %s", self.guild_id, error)

        async with self._lock:
            if not self._is_current_track_end_event(source, voice_client):
                return
            self._current_source = None
            self.session.stop_playback()

        self._log_track_end(error)

        if (self.session.autoplay_enabled or self.session.dj_enabled) and self.session.queue:
            logger.info(
                "Autoplay/DJ active for guild %s. autoplay_enabled=%s dj_enabled=%s queue_size=%s",
                self.guild_id,
                self.session.autoplay_enabled,
                self.session.dj_enabled,
                len(self.session.queue),
            )
            try:
                started = await self.play_next(voice_client)
            except Exception as exc:
                logger.warning("Autoplay failed in guild %s: %s", self.guild_id, exc)
                return
            if started is not None:
                await self._announce_now_playing(voice_client, started)

    def _is_current_track_end_event(self, source: object, voice_client: discord.VoiceClient) -> bool:
        current_source = self._current_source
        if current_source is None:
            return False

        if current_source is source:
            return True

        current_identifier = self._extract_track_match_token(current_source)
        event_identifier = self._extract_track_match_token(source)
        if current_identifier and event_identifier and current_identifier == event_identifier:
            return True

        return (
            isinstance(self._backend, LavalinkPlaybackBackend)
            and self.session.now_playing is not None
            and not self._backend.is_playing(voice_client)
        )

    @staticmethod
    def _extract_track_match_token(source: object) -> str | None:
        if source is None:
            return None

        if isinstance(source, dict):
            for key in ("identifier", "track", "uri", "url"):
                value = source.get(key)
                if value:
                    return str(value)

        for key in ("identifier", "track", "uri", "url"):
            value = getattr(source, key, None)
            if value:
                return str(value)

        return None

    async def _announce_now_playing(self, voice_client: discord.VoiceClient, track: Track) -> None:
        logger.info(
            "Announcing now playing for guild %s: %s (channel_id=%s)",
            self.guild_id,
            track.title,
            self.session.now_playing_channel_id,
        )
        channel_id = self.session.now_playing_channel_id
        if channel_id is None or voice_client.guild is None:
            logger.info(
                "Skipping now playing announcement for guild %s: channel_id=%s guild=%s",
                self.guild_id,
                channel_id,
                voice_client.guild is not None,
            )
            return

        channel = voice_client.guild.get_channel(channel_id)
        can_send = channel is not None and callable(getattr(channel, "send", None))
        if channel is None or not can_send:
            logger.info(
                "Skipping now playing announcement for guild %s: channel not found or invalid (%s)",
                self.guild_id,
                channel,
            )
            return

        await self._ensure_track_media(track)
        embed = build_now_playing_embed(track)
        await channel.send(embed=embed)

    async def _wait_for_opus_ready(self, track: Track) -> None:
        if not track.opus_url:
            return
        status_url = track.opus_url.rstrip("/") + "/status"
        deadline = asyncio.get_running_loop().time() + _OPUS_READY_TIMEOUT_SECONDS
        async with httpx.AsyncClient(timeout=10.0) as client:
            while True:
                try:
                    resp = await client.get(status_url)
                    if resp.status_code == 200:
                        payload = resp.json()
                        if payload.get("ready"):
                            return
                except Exception as exc:
                    logger.warning("Failed to check Opus status for guild %s: %s", self.guild_id, exc)
                    return
                if asyncio.get_running_loop().time() >= deadline:
                    return
                await asyncio.sleep(_OPUS_READY_POLL_SECONDS)

    async def _resolve_playback_url(self, track: Track) -> str:
        if self._backend.prefer_source_audio_url() and track.audio_url:
            # Prefer source MP3 for Lavalink. This avoids edge cases with cached opus
            # object URLs that may load but produce silent playback.
            return track.audio_url

        url = track.opus_url or track.audio_url
        if not url:
            raise ValueError("Track is missing an audio URL")
        if track.opus_url:
            try:
                async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                    async with client.stream("GET", url) as resp:
                        if resp.status_code >= 400:
                            raise ValueError(f"Opus URL returned {resp.status_code}")
                        return str(resp.url)
            except Exception as exc:
                logger.warning(
                    "Failed to resolve opus URL for guild %s: %s. Falling back to MP3.",
                    self.guild_id,
                    exc,
                )
                if track.audio_url:
                    return track.audio_url
        return url

    async def _ensure_track_media(self, track: Track) -> None:
        if track.media_url or not track.page_url:
            return
        try:
            data = await self._suno_client.fetch_track(track.page_url)
        except SunoScrapeError as exc:
            logger.warning("Failed to fetch media for guild %s: %s", self.guild_id, exc)
            return
        if data.media_url:
            track.media_url = data.media_url

    async def _probe_duration_seconds(self, url: str) -> float | None:
        def _run_probe() -> float | None:
            command = [
                _FFPROBE_PATH,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                url,
            ]
            try:
                result = subprocess.run(
                    command,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=_FFPROBE_TIMEOUT_SECONDS,
                )
                payload = json.loads(result.stdout or "{}")
                duration = payload.get("format", {}).get("duration")
                if duration is None:
                    return None
                return float(duration)
            except Exception as exc:
                logger.warning("ffprobe failed for guild %s: %s", self.guild_id, exc)
                return None

        return await asyncio.to_thread(_run_probe)

    async def _backfill_track_duration(self, track: Track, playback_url: str) -> None:
        duration = await self._probe_duration_seconds(playback_url)
        if duration is not None:
            track.duration_seconds = duration

    def _log_track_end(self, error: Exception | None) -> None:
        current = self.session.now_playing
        if current is None:
            return
        duration = current.duration_seconds
        if duration:
            logger.info(
                "Track ended in guild %s: %s (%.1fs)%s",
                self.guild_id,
                current.title,
                duration,
                f" error={error}" if error else "",
            )
        else:
            logger.info(
                "Track ended in guild %s: %s%s",
                self.guild_id,
                current.title,
                f" error={error}" if error else "",
            )


class AudioControllerManager:
    def __init__(self) -> None:
        self._controllers: dict[int, GuildAudioController] = {}

    def for_guild(self, guild_id: int, session: SessionState) -> GuildAudioController:
        if guild_id not in self._controllers:
            self._controllers[guild_id] = GuildAudioController(
                guild_id,
                session,
                backend=self._create_backend(guild_id),
            )
        return self._controllers[guild_id]

    def _create_backend(self, guild_id: int) -> PlaybackBackend:
        return LavalinkPlaybackBackend(guild_id)
