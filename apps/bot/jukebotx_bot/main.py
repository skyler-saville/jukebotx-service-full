# apps/bot/jukebotx_bot/main.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import inspect
import logging
import math
import os
from pathlib import Path
import re
import tempfile
import asyncio
from typing import Optional
from uuid import UUID, uuid4

import discord
from discord import app_commands
from discord.ext import commands
import httpx
import lavalink

from jukebotx_bot.discord.audio import AudioControllerManager
from jukebotx_bot.discord.now_playing import build_now_playing_embed
from jukebotx_bot.discord.playlist_download import (
    PlaylistArchiveTrack,
    build_playlist_archive_name,
    build_playlist_archive_part_filename,
    write_playlist_archive,
    write_playlist_archives,
)
from jukebotx_bot.discord.session import SessionManager, Track
from jukebotx_bot.discord.suno import extract_suno_urls
from jukebotx_bot.settings import load_bot_settings
from jukebotx_bot.voice.backends.lavalink import LavalinkPlaybackBackend
from jukebotx_bot.voice.service import JoinResult, VoiceOrchestrationService
from jukebotx_core.ports.repositories import TrackUpsert
from jukebotx_core.shared import build_playlist_archive_download_token
from jukebotx_core.use_cases.ingest_suno_links import IngestSunoLink, IngestSunoLinkInput
from jukebotx_infra.db import async_session_factory, init_db
from jukebotx_infra.repos.queue_repo import PostgresQueueRepository
from jukebotx_infra.repos.submission_repo import PostgresSubmissionRepository
from jukebotx_infra.repos.track_repo import PostgresTrackRepository
from jukebotx_infra.storage import OpusStorageConfig, OpusStorageService
from jukebotx_infra.suno.client import HttpxSunoClient, SunoScrapeError
from jukebotx_infra.suno.playlist_client import HttpxSunoPlaylistClient


def _is_mod(member: discord.Member) -> bool:
    """Return True if the member has moderation permissions or an allowed role."""
    perms = member.guild_permissions
    if perms.administrator or perms.manage_guild:
        return True

    allowed_roles = {"admin", "mod", "master of ceremonies", "dj"}
    return any(role.name.lower() in allowed_roles for role in member.roles)


def _is_master_user(*, user_id: int, master_user_id: int | None) -> bool:
    return master_user_id is not None and user_id == master_user_id


def _has_mod_access(member: discord.Member, *, master_user_id: int | None) -> bool:
    return _is_master_user(user_id=member.id, master_user_id=master_user_id) or _is_mod(member)


@dataclass(frozen=True)
class BotDeps:
    """
    Dependencies for the bot.
    Keeping these in one object makes lifecycle + testing much saner.
    """
    session_manager: SessionManager
    ingest_use_case: IngestSunoLink
    suno_client: HttpxSunoClient
    audio_manager: AudioControllerManager
    playlist_client: HttpxSunoPlaylistClient
    submission_repo: PostgresSubmissionRepository
    queue_repo: PostgresQueueRepository
    track_repo: PostgresTrackRepository
    voice_service: VoiceOrchestrationService
    lavalink_client: lavalink.Client | None = None


@dataclass(frozen=True)
class ResolvedPlaylistTrack:
    source_index: int
    title: str
    artist_display: str | None
    audio_url: str
    page_url: str | None
    media_url: str | None
    track_id: UUID | None


@dataclass(frozen=True)
class PlaylistArchiveDeliveryResult:
    mode: str
    added_count: int
    skipped_count: int
    skipped_titles: tuple[str, ...]
    part_count: int = 1


class JukeBot(commands.Bot):
    """
    Discord bot entrypoint for JukeBotx.

    Key rule:
    - Lifecycle hooks (setup_hook) own initialization.
    - Events/commands are registered in one place and use self.deps / self.settings.
    """

    def __init__(
        self,
        *,
        settings,
        deps: BotDeps,
        command_prefix: str,
        intents: discord.Intents,
    ) -> None:
        super().__init__(command_prefix=command_prefix, intents=intents)
        self.settings = settings
        self.deps = deps
        self._playlist_storage = OpusStorageService(
            OpusStorageConfig(
                provider=settings.opus_storage_provider,
                bucket=settings.opus_storage_bucket or "",
                prefix=settings.opus_storage_prefix,
                region=settings.opus_storage_region or "",
                endpoint_url=settings.opus_storage_endpoint_url or "",
                access_key_id=settings.opus_storage_access_key_id or "",
                secret_access_key=settings.opus_storage_secret_access_key or "",
                public_base_url=settings.opus_storage_public_base_url or "",
                signed_url_ttl_seconds=settings.opus_storage_signed_url_ttl_seconds,
                ttl_seconds=settings.opus_storage_ttl_seconds,
            )
        )

        logging.basicConfig(level=logging.INFO)

        self.remove_command("help")
        self._lavalink_socket_listener_registered = False
        self._gif_reaction_task: asyncio.Task[None] | None = None
        self._gif_reacted_submission_ids: set[int] = set()

        # Register events + commands once, right after construction.
        self._register_events()
        self._register_commands()
        self._register_slash_commands()

    async def setup_hook(self) -> None:
        """
        discord.py v2.x startup hook.
        Runs once, before on_ready, after the bot connects.
        """
        await init_db()
        await self._init_lavalink_client()
        await self._sync_app_commands()
        self._start_gif_reaction_task()

        # If you later convert cogs to extensions, load them here:
        # await self.load_extension("jukebotx_bot.discord.cogs.queue")
        # await self.load_extension("jukebotx_bot.discord.cogs.config")

    # -----------------------------
    # Internal helpers
    # -----------------------------
    def _get_session(self, ctx: commands.Context) -> SessionManager:
        return self.deps.session_manager

    def _get_audio(self, ctx: commands.Context) -> AudioControllerManager:
        return self.deps.audio_manager

    def _get_voice(self, ctx: commands.Context) -> VoiceOrchestrationService:
        return self.deps.voice_service

    def _build_opus_url(self, track_id: UUID | None) -> str | None:
        if track_id is None or self.settings.opus_api_base_url is None:
            return None
        base_url = self.settings.opus_api_base_url.rstrip("/")
        return f"{base_url}/tracks/{track_id}/opus"

    async def _sync_app_commands(self) -> None:
        guild_id = getattr(self.settings, "discord_guild_id", None)
        try:
            if guild_id is not None:
                guild = discord.Object(id=int(guild_id))
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
                logging.info("Synced %s app command(s) to guild %s.", len(synced), guild_id)
                return

            synced = await self.tree.sync()
            logging.info("Synced %s global app command(s).", len(synced))
        except discord.Forbidden as exc:
            if guild_id is not None:
                logging.warning(
                    "Skipping guild app-command sync for guild %s: missing access (%s). "
                    "Check DISCORD_GUILD_ID, bot membership, and applications.commands scope.",
                    guild_id,
                    exc,
                )
                return
            logging.warning("Skipping global app-command sync: missing access (%s).", exc)
        except Exception:
            logging.exception("Failed to sync app commands.")

    def _start_gif_reaction_task(self) -> None:
        if self._gif_reaction_task is None or self._gif_reaction_task.done():
            self._gif_reaction_task = asyncio.create_task(self._gif_reaction_loop())

    async def _gif_reaction_loop(self) -> None:
        while not self.is_closed():
            try:
                await self._process_gif_reactions_once()
            except Exception:
                logging.exception("GIF reaction poll failed.")
            await asyncio.sleep(10)

    async def _process_gif_reactions_once(self) -> None:
        updated_since = datetime.now(timezone.utc) - timedelta(hours=24)
        tracks = await self.deps.track_repo.fetch_recent_gif_tracks(updated_since=updated_since)
        for track in tracks:
            submissions = await self.deps.submission_repo.list_for_track(track_id=track.id)
            for submission in submissions:
                if submission.message_id in self._gif_reacted_submission_ids:
                    continue
                await self._add_gif_reaction(submission.channel_id, submission.message_id)
                self._gif_reacted_submission_ids.add(submission.message_id)

    async def _add_gif_reaction(self, channel_id: int, message_id: int) -> None:
        channel = self.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.fetch_channel(channel_id)
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                return

        if not isinstance(channel, discord.abc.Messageable):
            return

        try:
            message = await channel.fetch_message(message_id)
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            return

        try:
            await message.add_reaction("🎞️")
        except discord.HTTPException:
            return

    async def close(self) -> None:
        task = self._gif_reaction_task
        self._gif_reaction_task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await super().close()

    async def _prefetch_opus(self, track_id: UUID) -> None:
        if self.settings.opus_api_base_url is None:
            return
        status_url = f"{self.settings.opus_api_base_url.rstrip('/')}/tracks/{track_id}/opus/status"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.get(status_url)
        except Exception as exc:
            logging.warning("Failed to prefetch opus status for %s: %s", track_id, exc)

    @staticmethod
    def _format_byte_count(size_bytes: int) -> str:
        if size_bytes < 1024:
            return f"{size_bytes} B"
        if size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        return f"{size_bytes / (1024 * 1024):.1f} MB"

    @staticmethod
    def _format_duration_seconds(total_seconds: int) -> str:
        if total_seconds < 60:
            return f"{max(total_seconds, 1)} seconds"
        if total_seconds < 3600:
            minutes = max(round(total_seconds / 60), 1)
            unit = "minute" if minutes == 1 else "minutes"
            return f"{minutes} {unit}"
        hours = max(round(total_seconds / 3600), 1)
        unit = "hour" if hours == 1 else "hours"
        return f"{hours} {unit}"

    def _playlist_download_base_url(self) -> str | None:
        if self.settings.public_api_base_url:
            return self.settings.public_api_base_url.rstrip("/")
        if self.settings.web_base_url:
            return f"{self.settings.web_base_url.rstrip('/')}/api"
        return None

    def _can_send_playlist_download_link(self) -> bool:
        return bool(
            self._playlist_storage.is_enabled
            and self.settings.api_session_secret
            and self._playlist_download_base_url()
        )

    @staticmethod
    def _skipped_playlist_titles(skipped_items) -> tuple[str, ...]:
        return tuple(
            item.title or f"Track {item.source_index:02d}"
            for item in skipped_items[:5]
        )

    async def _build_playlist_archive_link(
        self,
        *,
        archive_path: Path,
        archive_name: str,
    ) -> str:
        base_url = self._playlist_download_base_url()
        secret = self.settings.api_session_secret
        if not self._playlist_storage.is_enabled or not base_url or not secret:
            raise RuntimeError("Playlist archive links are not configured.")

        prefix = self.settings.playlist_archive_storage_prefix.strip("/")
        object_name = f"{uuid4().hex}-{archive_name}"
        object_key = "/".join(part for part in (prefix, object_name) if part)

        await asyncio.to_thread(
            self._playlist_storage.upload_media_file,
            local_path=archive_path,
            object_key=object_key,
            content_type="application/zip",
        )

        token = build_playlist_archive_download_token(
            object_key=object_key,
            filename=archive_name,
            secret=secret,
            ttl_seconds=self.settings.playlist_download_link_ttl_seconds,
        )
        return f"{base_url}/downloads/playlists/{token}"

    async def _build_playlist_archive_tracks(self, items: list) -> list[PlaylistArchiveTrack]:
        resolved_tracks = [await self._resolve_playlist_track(item) for item in items]
        return [
            PlaylistArchiveTrack(
                source_index=track.source_index,
                title=track.title,
                artist_display=track.artist_display,
                audio_url=track.audio_url,
            )
            for track in resolved_tracks
        ]

    async def _resolve_playlist_track(self, item) -> ResolvedPlaylistTrack:
        track_title = item.suno_track_url or item.mp3_url
        audio_url = item.mp3_url
        page_url = item.suno_track_url
        artist_display = None
        media_url = None
        track_id: UUID | None = None

        if item.suno_track_url:
            try:
                cached_track = await self.deps.track_repo.get_by_suno_url(item.suno_track_url)
            except Exception:
                logging.exception("Failed to load cached track metadata for %s", item.suno_track_url)
            else:
                if cached_track is not None:
                    if cached_track.title:
                        track_title = cached_track.title
                    if cached_track.mp3_url:
                        audio_url = cached_track.mp3_url
                    page_url = cached_track.suno_url
                    artist_display = cached_track.artist_display
                    media_url = cached_track.video_url or cached_track.image_url
                    track_id = cached_track.id

        lookup_url = page_url or item.mp3_url
        if (track_title == (item.suno_track_url or item.mp3_url) or artist_display is None) and lookup_url is not None:
            try:
                fetched = await self.deps.suno_client.fetch_track(lookup_url)
            except SunoScrapeError as exc:
                logging.warning("Failed to enrich playlist item %s: %s", lookup_url, exc)
            else:
                stored_track = await self.deps.track_repo.upsert(
                    TrackUpsert(
                        suno_url=fetched.suno_url,
                        title=fetched.title,
                        artist_display=fetched.artist_display,
                        artist_username=fetched.artist_username,
                        lyrics=fetched.lyrics,
                        image_url=fetched.image_url,
                        video_url=fetched.video_url,
                        mp3_url=fetched.mp3_url,
                    )
                )
                if stored_track.title:
                    track_title = stored_track.title
                if stored_track.mp3_url:
                    audio_url = stored_track.mp3_url
                page_url = stored_track.suno_url
                artist_display = stored_track.artist_display
                media_url = stored_track.video_url or stored_track.image_url
                track_id = stored_track.id

        return ResolvedPlaylistTrack(
            source_index=item.source_index,
            title=track_title,
            artist_display=artist_display,
            audio_url=audio_url,
            page_url=page_url,
            media_url=media_url,
            track_id=track_id,
        )

    async def _build_playlist_archive(
        self,
        *,
        archive_tracks: list[PlaylistArchiveTrack],
        archive_path: Path,
    ):
        return await write_playlist_archive(
            tracks=archive_tracks,
            archive_path=archive_path,
        )

    async def _build_playlist_archives(
        self,
        *,
        archive_tracks: list[PlaylistArchiveTrack],
        output_dir: Path,
        max_archive_size_bytes: int,
    ):
        return await write_playlist_archives(
            tracks=archive_tracks,
            output_dir=output_dir,
            max_archive_size_bytes=max_archive_size_bytes,
        )

    async def _deliver_playlist_download(
        self,
        *,
        member: discord.Member,
        guild_name: str,
        playlist_url: str,
        items: list,
        filesize_limit: int,
    ) -> PlaylistArchiveDeliveryResult:
        archive_tracks = await self._build_playlist_archive_tracks(items)
        archive_name = build_playlist_archive_name(playlist_url)

        with tempfile.TemporaryDirectory(prefix="jukebotx-playlist-dl-") as tmp_dir:
            output_dir = Path(tmp_dir)
            archive_path = output_dir / archive_name
            single_summary = await self._build_playlist_archive(
                archive_tracks=archive_tracks,
                archive_path=archive_path,
            )

            if single_summary.added_count == 0:
                raise RuntimeError("I couldn't download any audio files from that playlist.")

            skipped_titles = self._skipped_playlist_titles(single_summary.skipped)
            dm_lines = [f"Here's your playlist zip from {guild_name}."]
            if single_summary.skipped_count:
                dm_lines.append(
                    "Skipped: "
                    + ", ".join(skipped_titles)
                    + ("." if single_summary.skipped_count <= 5 else ", and more.")
                )

            if archive_path.exists() and archive_path.stat().st_size <= filesize_limit:
                await member.send(
                    content="\n".join(dm_lines),
                    file=discord.File(archive_path, filename=archive_name),
                )
                return PlaylistArchiveDeliveryResult(
                    mode="attachment",
                    added_count=single_summary.added_count,
                    skipped_count=single_summary.skipped_count,
                    skipped_titles=skipped_titles,
                )

            if archive_path.exists() and self._can_send_playlist_download_link():
                try:
                    download_link = await self._build_playlist_archive_link(
                        archive_path=archive_path,
                        archive_name=archive_name,
                    )
                except Exception:
                    logging.exception("Failed to upload playlist archive for %s", playlist_url)
                else:
                    dm_lines.extend(
                        [
                            f"Download link: {download_link}",
                            "Archive size: "
                            f"{self._format_byte_count(archive_path.stat().st_size)}.",
                            "Link expires in about "
                            f"{self._format_duration_seconds(self.settings.playlist_download_link_ttl_seconds)}.",
                        ]
                    )
                    await member.send(content="\n".join(dm_lines))
                    return PlaylistArchiveDeliveryResult(
                        mode="link",
                        added_count=single_summary.added_count,
                        skipped_count=single_summary.skipped_count,
                        skipped_titles=skipped_titles,
                    )

            batch_summary = await self._build_playlist_archives(
                archive_tracks=archive_tracks,
                output_dir=output_dir / "parts",
                max_archive_size_bytes=filesize_limit,
            )
            if batch_summary.added_count == 0:
                raise RuntimeError("I couldn't download any audio files from that playlist.")

            skipped_titles = self._skipped_playlist_titles(batch_summary.skipped)
            dm_lines = [f"Here's your playlist zip from {guild_name}."]
            if batch_summary.part_count > 1:
                dm_lines.append(
                    f"Discord split it into {batch_summary.part_count} zip parts to fit the upload limit."
                )
            if batch_summary.skipped_count:
                dm_lines.append(
                    "Skipped: "
                    + ", ".join(skipped_titles)
                    + ("." if batch_summary.skipped_count <= 5 else ", and more.")
                )

            if batch_summary.part_count == 1:
                await member.send(
                    content="\n".join(dm_lines),
                    file=discord.File(
                        batch_summary.parts[0].local_path,
                        filename=build_playlist_archive_part_filename(
                            archive_name,
                            part_index=1,
                            part_count=batch_summary.part_count,
                        ),
                    ),
                )
            else:
                await member.send(content="\n".join(dm_lines))
                for part_index, part in enumerate(batch_summary.parts, start=1):
                    await member.send(
                        content=(
                            f"Playlist zip part {part_index}/{batch_summary.part_count} "
                            f"({self._format_byte_count(part.size_bytes)})"
                        ),
                        file=discord.File(
                            part.local_path,
                            filename=build_playlist_archive_part_filename(
                                archive_name,
                                part_index=part_index,
                                part_count=batch_summary.part_count,
                            ),
                        ),
                    )

            return PlaylistArchiveDeliveryResult(
                mode="multipart",
                added_count=batch_summary.added_count,
                skipped_count=batch_summary.skipped_count,
                skipped_titles=skipped_titles,
                part_count=batch_summary.part_count,
            )

    async def _init_lavalink_client(self) -> None:
        assert self.settings.lavalink_host is not None
        assert self.settings.lavalink_password is not None

        if self.user is None:
            raise RuntimeError("Discord client user is unavailable; cannot initialize Lavalink client user ID.")

        lavalink_client = lavalink.Client(int(self.user.id))
        LavalinkPlaybackBackend.configure_client(lavalink_client)

        add_node_kwargs: dict[str, object] = {
            "host": self.settings.lavalink_host,
            "port": self.settings.lavalink_port,
            "password": self.settings.lavalink_password,
            "region": "us",
            "name": "jukebotx-main",
            "ssl": self.settings.lavalink_secure,
        }

        add_node_params = inspect.signature(lavalink_client.add_node).parameters
        if "resume_key" in add_node_params:
            add_node_kwargs["resume_key"] = self.settings.lavalink_session_id
            if "resume_timeout" in add_node_params:
                add_node_kwargs["resume_timeout"] = self.settings.lavalink_resume_timeout_seconds
        elif "session_id" in add_node_params:
            add_node_kwargs["session_id"] = self.settings.lavalink_session_id
            if self.settings.lavalink_resume_timeout_seconds is not None:
                logging.warning(
                    "Installed lavalink client does not support resume_timeout; ignoring "
                    "LAVALINK_RESUME_TIMEOUT_SECONDS."
                )

        try:
            lavalink_client.add_node(**add_node_kwargs)
        except Exception:
            session = getattr(lavalink_client, "_session", None)
            if session is not None and hasattr(session, "close"):
                close_result = session.close()
                if inspect.isawaitable(close_result):
                    await close_result
            raise
        if not self._lavalink_socket_listener_registered:
            self.add_listener(lavalink_client.voice_update_handler, "on_socket_response")
            self._lavalink_socket_listener_registered = True
        object.__setattr__(self.deps, "lavalink_client", lavalink_client)

    # -----------------------------
    # Events
    # -----------------------------
    def _register_events(self) -> None:
        async def _send_submission_feedback(message: discord.Message, content: str) -> None:
            try:
                await message.author.send(content)
                return
            except discord.Forbidden:
                pass
            except discord.HTTPException:
                return

            try:
                await message.channel.send(f"{message.author.mention} {content}")
            except discord.HTTPException:
                return

        @self.event
        async def on_command_error(ctx: commands.Context, error: commands.CommandError) -> None:
            if isinstance(error, commands.CheckFailure):
                await ctx.send("🚫 You don’t have permission to use that command.")
                return

            if isinstance(error, commands.CommandNotFound):
                return

            # Show the actual error in chat during dev; remove later if you want.
            await ctx.send(f"⚠️ Command failed: {type(error).__name__}: {error}")
            raise error

        @self.event
        async def on_ready() -> None:
            """
            Fired when the client has connected and the bot identity is known.
            """
            assert self.user is not None, "client.user is unexpectedly None in on_ready()"
            self._sync_lavalink_user_id()

            bot_name = self.user.name.lower().strip()
            env = self.settings.env.lower().strip()

            # Production safety: prevent using a dev bot identity with production settings.
            assert (
                env != "production" or "dev" not in bot_name
            ), (
                "Safety check failed: ENV=production but the connected Discord bot name "
                "contains 'dev'. You are likely using the DEV bot token in production."
            )

            # Development safety: prevent using prod bot identity in development.
            assert (
                env != "development" or "dev" in bot_name
            ), (
                "Safety check failed: ENV=development but the connected Discord bot name "
                "does NOT contain 'dev'. You are likely using the production bot token in development."
            )

            print(f"Connected as {self.user} (env={self.settings.env})")

        @self.event
        async def on_socket_response(payload: dict[str, object]) -> None:
            if self._lavalink_socket_listener_registered:
                return
            lavalink_client = self.deps.lavalink_client
            if lavalink_client is None:
                return

            event_type = payload.get("t")
            if event_type not in {"VOICE_SERVER_UPDATE", "VOICE_STATE_UPDATE"}:
                return

            handler = getattr(lavalink_client, "voice_update_handler", None)
            if handler is None:
                logging.warning("Lavalink client is missing voice_update_handler; voice updates were dropped.")
                return

            try:
                result = handler(payload)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                logging.exception("Failed to forward Discord voice update event to Lavalink.")

        @self.event
        async def on_message(message: discord.Message) -> None:
            """
            Ingest Suno URLs from messages when the bot is active in the guild VC.
            Invokes prefix commands before attempting auto-ingest.
            """
            if message.author.bot:
                return

            ctx = await self.get_context(message)
            if ctx.command is not None:
                await self.invoke(ctx)
                return

            if ctx.invoked_with:
                return

            # DMs: still allow commands to process.
            if message.guild is None:
                return

            # Only auto-ingest when bot is currently connected in the guild.
            if message.guild.voice_client is None:
                await self.process_commands(message)
                return

            urls = extract_suno_urls(message.content)
            if not urls:
                await self.process_commands(message)
                return

            added_any = False
            skipped_playlist = False
            blocked_reason: str | None = None
            limit_reached = False

            session = self.deps.session_manager.for_guild(message.guild.id)
            is_host = (
                isinstance(message.author, discord.Member)
                and _has_mod_access(message.author, master_user_id=self.settings.master_user_id)
            )
            user_id = message.author.id
            remaining_slots: int | None = None

            if not is_host:
                if not session.submissions_open:
                    blocked_reason = "Submissions are closed right now."
                else:
                    if session.per_user_limit is not None:
                        current = session.per_user_counts.get(user_id, 0)
                        remaining_slots = session.per_user_limit - current
                        if remaining_slots <= 0:
                            blocked_reason = "You have reached the submission limit for this session."
                    if blocked_reason is None:
                        cooldown_remaining = session.cooldown_remaining(user_id)
                        if cooldown_remaining > 0:
                            blocked_reason = (
                                "You're on cooldown for another "
                                f"{math.ceil(cooldown_remaining)}s before submitting again."
                            )
            for url in urls:
                if "https://suno.com/playlist/" in url:
                    skipped_playlist = True
                    continue
                if blocked_reason is not None:
                    continue
                if remaining_slots is not None and remaining_slots <= 0:
                    limit_reached = True
                    break
                try:
                    result = await self.deps.ingest_use_case.execute(
                        IngestSunoLinkInput(
                            guild_id=message.guild.id,
                            channel_id=message.channel.id,
                            message_id=message.id,
                            author_id=message.author.id,
                            suno_url=url,
                        )
                    )
                except SunoScrapeError as exc:
                    print(f"Failed to ingest Suno URL {url}: {exc}")
                    continue

                if not result.mp3_url:
                    logging.warning("Skipping Suno URL without mp3_url: %s", url)
                    await message.channel.send(
                        "I found that Suno track, but Suno did not expose a playable MP3 URL for it, so I couldn't queue it."
                    )
                    continue

                opus_url = self._build_opus_url(result.track_id)

                track = Track(
                    audio_url=result.mp3_url,
                    opus_url=opus_url,
                    page_url=result.suno_url,
                    title=result.track_title or url,
                    artist_display=result.artist_display,
                    media_url=result.media_url,
                    requester_id=message.author.id,
                    requester_name=getattr(message.author, "display_name", "unknown"),
                )
                session.queue.append(track)
                session.per_user_counts[track.requester_id] = session.per_user_counts.get(track.requester_id, 0) + 1
                asyncio.create_task(self._prefetch_opus(result.track_id))
                added_any = True
                if remaining_slots is not None:
                    remaining_slots -= 1


            if added_any:
                session.mark_submission(user_id)
                try:
                    await message.add_reaction("🤘")
                except discord.HTTPException:
                    pass
            if blocked_reason is not None:
                await _send_submission_feedback(message, blocked_reason)
            elif limit_reached:
                await _send_submission_feedback(
                    message,
                    "You have reached the submission limit for this session. "
                    "Additional songs were not queued.",
                )

            if skipped_playlist:
                await message.channel.send("Playlist links aren’t auto-ingested. Use `;playlist <url>` instead.")

            await self.process_commands(message)

    def _sync_lavalink_user_id(self) -> None:
        lavalink_client = self.deps.lavalink_client
        if lavalink_client is None or self.user is None:
            return

        user_id = int(self.user.id)
        setter = getattr(lavalink_client, "set_user_id", None)
        if callable(setter):
            setter(user_id)
            return

        synced = False
        for attr_name in ("user_id", "_user_id"):
            if not hasattr(lavalink_client, attr_name):
                continue
            try:
                setattr(lavalink_client, attr_name, user_id)
                synced = True
            except Exception:
                logging.exception("Failed setting Lavalink client %s to %s.", attr_name, user_id)

        if not synced:
            logging.warning(
                "Could not sync Lavalink client user ID. Voice sessions may fail to establish."
            )

    # -----------------------------
    # Commands
    # -----------------------------
    def _register_slash_commands(self) -> None:
        async def _respond(
            interaction: discord.Interaction,
            *,
            content: str | None = None,
            embed: discord.Embed | None = None,
            ephemeral: bool = False,
        ) -> None:
            if interaction.response.is_done():
                await interaction.followup.send(content=content, embed=embed, ephemeral=ephemeral)
            else:
                await interaction.response.send_message(content=content, embed=embed, ephemeral=ephemeral)

        def _require_member(interaction: discord.Interaction) -> discord.Member | None:
            if interaction.guild is None or not isinstance(interaction.user, discord.Member):
                return None
            return interaction.user

        def _member_has_mod_access(member: discord.Member) -> bool:
            return _has_mod_access(member, master_user_id=self.settings.master_user_id)

        def _member_is_master_user(member: discord.Member) -> bool:
            return _is_master_user(user_id=member.id, master_user_id=self.settings.master_user_id)

        async def _ensure_mod(interaction: discord.Interaction) -> discord.Member | None:
            member = _require_member(interaction)
            if member is None:
                await _respond(interaction, content="This command can only be used in a server.", ephemeral=True)
                return None
            if not _member_has_mod_access(member):
                await _respond(interaction, content="You don't have permission to use this command.", ephemeral=True)
                return None
            return member

        async def _ensure_master_user(interaction: discord.Interaction) -> discord.Member | None:
            member = _require_member(interaction)
            if member is None:
                await _respond(interaction, content="This command can only be used in a server.", ephemeral=True)
                return None
            if not _member_is_master_user(member):
                await _respond(interaction, content="You don't have permission to use this command.", ephemeral=True)
                return None
            return member

        @self.tree.command(name="join", description="Join your current voice channel (mods).")
        async def join_slash(interaction: discord.Interaction) -> None:
            member = await _ensure_mod(interaction)
            if member is None:
                return
            assert interaction.guild is not None

            if member.voice is None or member.voice.channel is None:
                await _respond(interaction, content="You're not in a voice channel!", ephemeral=True)
                return

            channel = member.voice.channel
            try:
                outcome = await self.deps.voice_service.join(interaction.guild, channel)
            except discord.Forbidden:
                await _respond(
                    interaction,
                    content="I don't have permission to join that voice channel (View/Connect).",
                    ephemeral=True,
                )
                return
            except Exception as exc:
                await _respond(interaction, content=f"Failed to join VC: {type(exc).__name__}: {exc}", ephemeral=True)
                raise

            if outcome.result == JoinResult.ALREADY_IN_CHANNEL:
                await _respond(interaction, content=f"I'm already in {outcome.channel_name}.")
                return
            if outcome.result == JoinResult.MOVED:
                await _respond(interaction, content=f"Moved to {outcome.channel_name}!")
                return

            await _respond(interaction, content=f"Joined {outcome.channel_name}!")

        @self.tree.command(name="leave", description="Leave the voice channel and reset session (mods).")
        async def leave_slash(interaction: discord.Interaction) -> None:
            member = await _ensure_mod(interaction)
            if member is None:
                return
            assert interaction.guild is not None

            session = self.deps.session_manager.for_guild(interaction.guild.id)
            session.reset()
            await self.deps.voice_service.leave(interaction.guild)
            await self.deps.queue_repo.clear(guild_id=interaction.guild.id)

            channel_id = interaction.channel_id
            if channel_id is not None:
                await self.deps.submission_repo.clear_for_channel(
                    guild_id=interaction.guild.id,
                    channel_id=channel_id,
                )

            await _respond(interaction, content="Left the voice channel. Session reset.")

        @self.tree.command(name="queue", description="Show current queue and session status.")
        async def queue_slash(interaction: discord.Interaction) -> None:
            member = _require_member(interaction)
            if member is None:
                await _respond(interaction, content="This command can only be used in a server.", ephemeral=True)
                return
            assert interaction.guild is not None

            session = self.deps.session_manager.for_guild(interaction.guild.id)
            lines: list[str] = []
            if session.submissions_open:
                lines.append("Session is open.")
                if _member_has_mod_access(member):
                    lines.append("Add a Suno URL to queue a song, or use /playlist.")
                else:
                    lines.append("Add a Suno URL to queue a song.")
            else:
                lines.append("Session is closed.")

            if session.queue:
                total = len(session.queue)
                if total == 1:
                    lines.append("Last song")
                elif total > 5:
                    lines.append(f"Next 5 out of {total}")
                else:
                    lines.append(f"Next {total}")
                for idx, track in enumerate(session.queue[:5], start=1):
                    lines.append(f"{idx}. {track.title} (requested by {track.requester_name})")
            else:
                lines.append("Queue is empty.")

            await _respond(interaction, content="\n".join(lines))

        @self.tree.command(name="nowplaying", description="Show now playing information.")
        async def now_playing_slash(interaction: discord.Interaction) -> None:
            member = _require_member(interaction)
            if member is None:
                await _respond(interaction, content="This command can only be used in a server.", ephemeral=True)
                return
            assert interaction.guild is not None

            session = self.deps.session_manager.for_guild(interaction.guild.id)
            if session.now_playing is None:
                await _respond(interaction, content="Nothing is playing.")
                return

            embed = build_now_playing_embed(
                session.now_playing,
                started_at=session.now_playing_started_at,
            )
            await _respond(interaction, embed=embed)

        @self.tree.command(name="play", description="Start playback of the current queue.")
        async def play_slash(interaction: discord.Interaction) -> None:
            member = _require_member(interaction)
            if member is None:
                await _respond(interaction, content="This command can only be used in a server.", ephemeral=True)
                return
            assert interaction.guild is not None

            session = self.deps.session_manager.for_guild(interaction.guild.id)
            session.now_playing_channel_id = interaction.channel_id
            if session.now_playing is not None:
                await _respond(interaction, content=f"Already playing: {session.now_playing.title}. Use /skip.")
                return

            if not session.queue:
                if _member_has_mod_access(member):
                    await _respond(interaction, content="Queue is empty. Drop a Suno URL or use /playlist.")
                else:
                    await _respond(interaction, content="Queue is empty. Drop a Suno URL.")
                return

            started = await self.deps.voice_service.play_next(interaction.guild)
            if started is None:
                await _respond(interaction, content="Could not start playback. Ensure the bot is connected to voice.")
                return

            session.now_playing_channel_id = interaction.channel_id
            await _respond(
                interaction,
                embed=build_now_playing_embed(
                    started,
                    started_at=session.now_playing_started_at,
                ),
            )

        @self.tree.command(name="skip", description="Skip the current track (mods).")
        async def skip_slash(interaction: discord.Interaction) -> None:
            member = await _ensure_mod(interaction)
            if member is None:
                return
            assert interaction.guild is not None

            if interaction.guild.voice_client is None:
                await _respond(interaction, content="I'm not connected to a voice channel.", ephemeral=True)
                return

            session = self.deps.session_manager.for_guild(interaction.guild.id)
            started = await self.deps.voice_service.skip(interaction.guild)
            if started is None:
                await _respond(interaction, content="Skipped. Queue is now empty; playback stopped.")
                return

            session.now_playing_channel_id = interaction.channel_id
            await _respond(
                interaction,
                content="Skipped.",
                embed=build_now_playing_embed(
                    started,
                    started_at=session.now_playing_started_at,
                ),
            )

        @self.tree.command(name="stop", description="Stop playback (mods).")
        async def stop_slash(interaction: discord.Interaction) -> None:
            member = await _ensure_mod(interaction)
            if member is None:
                return
            assert interaction.guild is not None

            if interaction.guild.voice_client is None:
                await _respond(interaction, content="I'm not connected to a voice channel.", ephemeral=True)
                return

            await self.deps.voice_service.stop(interaction.guild)
            await _respond(interaction, content="Playback stopped.")

        @self.tree.command(name="playlist", description="Queue a Suno playlist URL (mods).")
        @app_commands.describe(url="Suno playlist URL")
        async def playlist_slash(interaction: discord.Interaction, url: str) -> None:
            member = await _ensure_mod(interaction)
            if member is None:
                return
            assert interaction.guild is not None

            if interaction.guild.voice_client is None:
                await _respond(interaction, content="Use /join first.", ephemeral=True)
                return
            if "https://suno.com/playlist/" not in url:
                await _respond(
                    interaction,
                    content="Please provide a Suno playlist URL like https://suno.com/playlist/....",
                    ephemeral=True,
                )
                return

            session = self.deps.session_manager.for_guild(interaction.guild.id)
            session.now_playing_channel_id = interaction.channel_id
            session.submissions_open = False
            session.queue.clear()

            await _respond(
                interaction,
                content="Cleared the queue and closed submissions. Fetching playlist and queuing tracks...",
            )

            try:
                playlist_data = await self.deps.playlist_client.fetch_playlist(url)
            except SunoScrapeError as exc:
                await interaction.followup.send(f"Failed to fetch playlist: {exc}")
                return

            if not playlist_data.items:
                await interaction.followup.send("No songs were found in that playlist.")
                return

            user_id = member.id
            for item in playlist_data.items:
                track_title = item.suno_track_url or item.mp3_url
                audio_url = item.mp3_url
                page_url = item.suno_track_url
                artist_display = None
                media_url = None
                opus_url = None
                track_id: UUID | None = None

                ingest_url = item.suno_track_url or item.mp3_url
                if ingest_url is not None:
                    try:
                        ingest_result = await self.deps.ingest_use_case.execute(
                            IngestSunoLinkInput(
                                guild_id=interaction.guild.id,
                                channel_id=interaction.channel_id or 0,
                                message_id=0,
                                author_id=member.id,
                                suno_url=ingest_url,
                            )
                        )
                    except SunoScrapeError as exc:
                        logging.warning("Failed to ingest Suno URL %s: %s", ingest_url, exc)
                    else:
                        if ingest_result.track_title:
                            track_title = ingest_result.track_title
                        if ingest_result.mp3_url:
                            audio_url = ingest_result.mp3_url
                        page_url = ingest_result.suno_url
                        artist_display = ingest_result.artist_display
                        media_url = ingest_result.media_url
                        opus_url = self._build_opus_url(ingest_result.track_id)
                        track_id = ingest_result.track_id

                track = Track(
                    audio_url=audio_url,
                    opus_url=opus_url,
                    page_url=page_url,
                    title=track_title,
                    artist_display=artist_display,
                    media_url=media_url,
                    requester_id=member.id,
                    requester_name=member.display_name,
                )
                session.queue.append(track)
                session.per_user_counts[user_id] = session.per_user_counts.get(user_id, 0) + 1
                if track_id is not None:
                    asyncio.create_task(self._prefetch_opus(track_id))

            await interaction.followup.send(
                f"Queued {len(playlist_data.items)} track(s) from the playlist. Submissions are now closed."
            )

        @self.tree.command(name="playlist-dl", description="DM a zip of a Suno playlist (God-tier).")
        @app_commands.describe(url="Suno playlist URL")
        async def playlist_download_slash(interaction: discord.Interaction, url: str) -> None:
            member = await _ensure_master_user(interaction)
            if member is None:
                return

            if "https://suno.com/playlist/" not in url:
                await _respond(
                    interaction,
                    content="Please provide a Suno playlist URL like https://suno.com/playlist/....",
                    ephemeral=True,
                )
                return

            await _respond(interaction, content="Fetching playlist and building your zip...", ephemeral=True)

            try:
                playlist_data = await self.deps.playlist_client.fetch_playlist(url)
            except SunoScrapeError as exc:
                await interaction.followup.send(f"Failed to fetch playlist: {exc}", ephemeral=True)
                return

            if not playlist_data.items:
                await interaction.followup.send("No songs were found in that playlist.", ephemeral=True)
                return

            try:
                result = await self._deliver_playlist_download(
                    member=member,
                    guild_name=interaction.guild.name if interaction.guild else "JukeBotx",
                    playlist_url=playlist_data.playlist_url,
                    items=playlist_data.items,
                    filesize_limit=interaction.filesize_limit,
                )
            except RuntimeError as exc:
                await interaction.followup.send(str(exc), ephemeral=True)
                return
            except discord.Forbidden:
                await interaction.followup.send(
                    "I couldn't DM you the playlist download. Please enable DMs and try again.",
                    ephemeral=True,
                )
                return
            except discord.HTTPException as exc:
                await interaction.followup.send(
                    f"Discord couldn't send the playlist download right now: {exc}",
                    ephemeral=True,
                )
                return
            except Exception as exc:
                logging.exception("Failed to build playlist archive for %s", playlist_data.playlist_url)
                await interaction.followup.send(
                    f"Failed to build the playlist zip: {type(exc).__name__}: {exc}",
                    ephemeral=True,
                )
                return

            skipped_note = ""
            if result.skipped_count:
                skipped_note = (
                    f" Downloaded {result.added_count}/{len(playlist_data.items)} track(s); "
                    f"skipped {result.skipped_count}."
                )

            if result.mode == "link":
                success_message = f"Sent a playlist download link to your DMs.{skipped_note}"
            elif result.mode == "attachment":
                success_message = f"Sent the playlist zip to your DMs.{skipped_note}"
            else:
                success_message = (
                    f"Sent {result.part_count} archive part(s) to your DMs.{skipped_note}"
                )

            await interaction.followup.send(success_message, ephemeral=True)

        admin_group = app_commands.Group(
            name="admin",
            description="Admin and DJ controls for session management.",
        )

        @admin_group.command(name="submissions", description="Open or close submissions (mods).")
        @app_commands.describe(state="Open or close submissions")
        @app_commands.choices(
            state=[
                app_commands.Choice(name="open", value="open"),
                app_commands.Choice(name="close", value="close"),
            ]
        )
        async def admin_submissions(interaction: discord.Interaction, state: app_commands.Choice[str]) -> None:
            member = await _ensure_mod(interaction)
            if member is None:
                return
            assert interaction.guild is not None

            session = self.deps.session_manager.for_guild(interaction.guild.id)
            if state.value == "open":
                session.submissions_open = True
                session.reset_submission_counts()
                await _respond(interaction, content="Submissions are open.")
                return

            session.submissions_open = False
            await _respond(interaction, content="Submissions are closed.")

        @admin_group.command(name="limit", description="Set per-user submission limit (mods).")
        async def admin_limit(interaction: discord.Interaction, count: app_commands.Range[int, 1, 100]) -> None:
            member = await _ensure_mod(interaction)
            if member is None:
                return
            assert interaction.guild is not None

            session = self.deps.session_manager.for_guild(interaction.guild.id)
            session.per_user_limit = count
            await _respond(interaction, content=f"Per-user submission limit set to {count}.")

        @admin_group.command(name="autoplay", description="Configure autoplay mode (mods).")
        @app_commands.describe(
            mode="off, until queue empty, or for a specific number of tracks",
            count="Required only when mode is count",
        )
        @app_commands.choices(
            mode=[
                app_commands.Choice(name="until_empty", value="until_empty"),
                app_commands.Choice(name="count", value="count"),
                app_commands.Choice(name="off", value="off"),
            ]
        )
        async def admin_autoplay(
            interaction: discord.Interaction,
            mode: app_commands.Choice[str],
            count: app_commands.Range[int, 1, 100] | None = None,
        ) -> None:
            member = await _ensure_mod(interaction)
            if member is None:
                return
            assert interaction.guild is not None

            session = self.deps.session_manager.for_guild(interaction.guild.id)
            session.now_playing_channel_id = interaction.channel_id

            if mode.value == "off":
                session.disable_autoplay()
                await _respond(interaction, content="Autoplay disabled.")
                return
            if mode.value == "until_empty":
                session.set_autoplay(None)
                await _respond(interaction, content="Autoplay enabled until queue is empty.")
                return
            if count is None:
                await _respond(interaction, content="Count is required when mode is 'count'.", ephemeral=True)
                return

            session.set_autoplay(count)
            await _respond(interaction, content=f"Autoplay enabled for the next {count} track(s).")

        @admin_group.command(name="dj", description="Configure DJ mode (mods).")
        @app_commands.describe(
            mode="off, until queue empty, or for a specific number of tracks",
            count="Required only when mode is count",
        )
        @app_commands.choices(
            mode=[
                app_commands.Choice(name="until_empty", value="until_empty"),
                app_commands.Choice(name="count", value="count"),
                app_commands.Choice(name="off", value="off"),
            ]
        )
        async def admin_dj(
            interaction: discord.Interaction,
            mode: app_commands.Choice[str],
            count: app_commands.Range[int, 1, 100] | None = None,
        ) -> None:
            member = await _ensure_mod(interaction)
            if member is None:
                return
            assert interaction.guild is not None

            session = self.deps.session_manager.for_guild(interaction.guild.id)
            session.now_playing_channel_id = interaction.channel_id

            if mode.value == "off":
                session.disable_dj()
                await _respond(interaction, content="DJ mode disabled.")
                return
            if mode.value == "until_empty":
                session.set_dj(None)
                await _respond(interaction, content="DJ mode enabled until queue is empty.")
                return
            if count is None:
                await _respond(interaction, content="Count is required when mode is 'count'.", ephemeral=True)
                return

            session.set_dj(count)
            await _respond(interaction, content=f"DJ mode enabled for the next {count} track(s).")

        @admin_group.command(name="clear", description="Clear queue (mods).")
        async def admin_clear(interaction: discord.Interaction) -> None:
            member = await _ensure_mod(interaction)
            if member is None:
                return
            assert interaction.guild is not None

            session = self.deps.session_manager.for_guild(interaction.guild.id)
            session.queue.clear()
            await _respond(interaction, content="Queue cleared.")

        @admin_group.command(name="remove", description="Remove queue item by position (mods).")
        async def admin_remove(interaction: discord.Interaction, index: app_commands.Range[int, 1, 100]) -> None:
            member = await _ensure_mod(interaction)
            if member is None:
                return
            assert interaction.guild is not None

            session = self.deps.session_manager.for_guild(interaction.guild.id)
            if index > len(session.queue):
                await _respond(interaction, content="Invalid queue index.", ephemeral=True)
                return

            track = session.queue.pop(index - 1)
            await _respond(interaction, content=f"Removed: {track.title} (requested by {track.requester_name}).")

        self.tree.add_command(admin_group)

    def _register_commands(self) -> None:
        def _ctx_has_mod_access(ctx: commands.Context) -> bool:
            return (
                isinstance(ctx.author, discord.Member)
                and _has_mod_access(ctx.author, master_user_id=self.settings.master_user_id)
            )

        def _ctx_is_master_user(ctx: commands.Context) -> bool:
            return _is_master_user(user_id=ctx.author.id, master_user_id=self.settings.master_user_id)

        @self.command(name="help")
        async def help_command(ctx: commands.Context) -> None:
            is_mod = _ctx_has_mod_access(ctx)
            is_master = _ctx_is_master_user(ctx)
            embed = discord.Embed(
                title="JukeBotx Help",
                description=(
                    "Command prefix: `;`\n"
                    "Drop Suno links in chat to queue when submissions are open. "
                    + ("Use `;playlist <url>` for Suno playlists (mods only)." if is_mod else "")
                ),
                color=discord.Color.orange() if is_mod else discord.Color.blurple(),
            )
            embed.add_field(
                name="Session",
                value=(
                    "`;join` — Join your voice channel (mods).\n"
                    "`;leave` — Leave and reset the session (mods).\n"
                    "`;open` / `;close` — Toggle submissions (mods).\n"
                    "`;web` — Share the session web URL.\n"
                    "`;setlist` — DM the current session setlist."
                ),
                inline=False,
            )
            embed.add_field(
                name="Queue + Playback",
                value=(
                    "`;q` — Show the queue and session status.\n"
                    "`;p` — Start playback of the queue.\n"
                    "`;np` — Show now playing info.\n"
                    "`;n` — Skip the current track (mods).\n"
                    "`;s` — Stop playback (mods)."
                ),
                inline=False,
            )
            if is_mod:
                queue_management_lines = [
                    "`;playlist <url>` — Queue a Suno playlist and close submissions.",
                    "`;clear` — Clear the queue.",
                    "`;remove <index>` — Remove a queued item.",
                    "`;limit <count>` — Set per-user submission limit.",
                ]
                if is_master:
                    queue_management_lines.insert(
                        1,
                        "`;playlist-dl <url>` or `/playlist-dl <url>` — DM a zip of a Suno playlist.",
                    )

                embed.add_field(
                    name="Queue Management (mods)",
                    value="\n".join(queue_management_lines),
                    inline=False,
                )
                embed.add_field(
                    name="Autoplay + DJ Mode (mods)",
                    value=(
                        "`;autoplay` — Enable autoplay until the queue ends.\n"
                        "`;autoplay <count>` — Play the next N tracks.\n"
                        "`;autoplay off` — Disable autoplay.\n"
                        "`;cooldown` / `;cooldown <minutes>` / `;cooldown off` — Toggle submission cooldown.\n"
                        "`;dj` / `;dj <count>` / `;dj off` — Toggle DJ mode."
                    ),
                    inline=False,
                )
                embed.add_field(
                    name="Announcements (mods)",
                    value="`;ping here <message>` or `;ping jamsession <message>` — Ping channels/roles.",
                    inline=False,
                )
            embed.set_footer(text="Need help? Ask a mod or use ;help anytime.")
            await ctx.send(embed=embed)

        @self.command(name="join")
        async def join(ctx: commands.Context) -> None:
            if ctx.guild is None:
                await ctx.send("This command can only be used in a server.")
                return

            if not _ctx_has_mod_access(ctx):
                await ctx.send("You don't have permission to use this command.")
                return

            if ctx.author.voice is None or ctx.author.voice.channel is None:
                await ctx.send("You're not in a voice channel!")
                return

            channel = ctx.author.voice.channel
            try:
                outcome = await self._get_voice(ctx).join(ctx.guild, channel)
            except discord.Forbidden:
                await ctx.send("🚫 I don't have permission to join that voice channel (View/Connect).")
                return
            except Exception as exc:
                await ctx.send(f"⚠️ Failed to join VC: {type(exc).__name__}: {exc}")
                raise

            if outcome.result == JoinResult.ALREADY_IN_CHANNEL:
                await ctx.send(f"I'm already in {outcome.channel_name}.")
                return

            if outcome.result == JoinResult.MOVED:
                await ctx.send(f"Moved to {outcome.channel_name}!")
                return

            await ctx.send(f"Joined {outcome.channel_name}!")


        @self.command(name="leave")
        async def leave(ctx: commands.Context) -> None:
            if ctx.guild is None:
                await ctx.send("This command can only be used in a server.")
                return

            if not _ctx_has_mod_access(ctx):
                await ctx.send("You don't have permission to use this command.")
                return

            session = self._get_session(ctx).for_guild(ctx.guild.id)
            session.reset()

            await self._get_voice(ctx).leave(ctx.guild)

            await self.deps.queue_repo.clear(guild_id=ctx.guild.id)
            await self.deps.submission_repo.clear_for_channel(
                guild_id=ctx.guild.id,
                channel_id=ctx.channel.id,
            )

            await ctx.send("Left the voice channel. Session reset.")

        @self.command(name="setlist")
        async def setlist(ctx: commands.Context) -> None:
            if ctx.guild is None:
                await ctx.send("This command can only be used in a server.")
                return

            if not _ctx_has_mod_access(ctx):
                await ctx.send("You don't have permission to use this command.")
                return

            if ctx.author.voice is None or ctx.author.voice.channel is None:
                await ctx.send("You're not in a voice channel!")
                return

            tracks = await self.deps.submission_repo.list_tracks_for_channel(
                guild_id=ctx.guild.id,
                channel_id=ctx.channel.id,
            )
            if not tracks:
                await ctx.send("No songs found for this session yet.")
                return

            channel_name = ctx.author.voice.channel.name.lower().strip()
            channel_slug = re.sub(r"[^a-z0-9]+", "_", channel_name).strip("_") or "session"
            date_stamp = datetime.now(timezone.utc).strftime("%b_%d_%Y").lower()
            filename = f"{channel_slug}_{date_stamp}.txt"

            lines = []
            for track in tracks:
                artist = track.artist_display or "Unknown Artist"
                title = track.title or "Untitled"
                url = track.suno_url or track.mp3_url or ""
                lines.append(f"{artist} - {title} - {url}")

            with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as tmp_file:
                tmp_file.write("\n".join(lines))
                tmp_path = tmp_file.name

            try:
                await ctx.author.send(
                    content="Here's your session setlist!",
                    file=discord.File(tmp_path, filename=filename),
                )
            except discord.Forbidden:
                await ctx.send("I couldn't DM you the setlist. Please enable DMs and try again.")
                return
            finally:
                try:
                    os.remove(tmp_path)
                except OSError:
                    logging.warning("Failed to delete temp setlist file: %s", tmp_path)

            await ctx.send("Setlist sent via DM.")

        @self.command(name="ping")
        async def ping(ctx: commands.Context, target: str, *, message: str) -> None:
            if ctx.guild is None or not isinstance(ctx.author, discord.Member):
                await ctx.send("This command can only be used in a server.")
                return

            if not _ctx_has_mod_access(ctx):
                await ctx.send("You don't have permission to use this command.")
                return

            target_norm = target.lower().strip()
            if target_norm not in {"here", "jamsession"}:
                await ctx.send("Target must be 'here' or 'jamsession'.")
                return

            if self.settings.jam_session_channel_id is None:
                await ctx.send("Jam session channel is not configured.")
                return

            channel = ctx.guild.get_channel(self.settings.jam_session_channel_id)
            if channel is None:
                await ctx.send("Jam session channel not found.")
                return

            if target_norm == "here":
                mention = "@here"
            else:
                if self.settings.jam_session_role_id is None:
                    await ctx.send("Jam session role is not configured.")
                    return
                mention = f"<@&{self.settings.jam_session_role_id}>"

            await channel.send(f"{mention} Submissions are open! {message}")
            await ctx.send("Announcement sent.")

        @self.command(name="open")
        async def open_submissions(ctx: commands.Context) -> None:
            if ctx.guild is None:
                await ctx.send("This command can only be used in a server.")
                return

            if not _ctx_has_mod_access(ctx):
                await ctx.send("You don't have permission to use this command.")
                return

            session = self._get_session(ctx).for_guild(ctx.guild.id)
            session.submissions_open = True
            session.reset_submission_counts()
            await ctx.send("Submissions are open.")

        @self.command(name="close")
        async def close_submissions(ctx: commands.Context) -> None:
            if ctx.guild is None:
                await ctx.send("This command can only be used in a server.")
                return

            if not _ctx_has_mod_access(ctx):
                await ctx.send("You don't have permission to use this command.")
                return

            session = self._get_session(ctx).for_guild(ctx.guild.id)
            session.submissions_open = False
            await ctx.send("Submissions are closed.")

        @self.command(name="web", aliases=["sessionurl"])
        async def web(ctx: commands.Context) -> None:
            if ctx.guild is None:
                await ctx.send("This command can only be used in a server.")
                return

            if not _ctx_has_mod_access(ctx):
                await ctx.send("You don't have permission to use this command.")
                return

            if self.settings.web_base_url is None:
                await ctx.send("Web UI base URL is not configured.")
                return

            base_url = self.settings.web_base_url.rstrip("/")
            url = (
                f"{base_url}/guilds/{ctx.guild.id}"
                f"/channels/{ctx.channel.id}/session/tracks"
            )

            target_channel = ctx.channel
            if self.settings.jam_session_channel_id is not None:
                configured_channel = ctx.guild.get_channel(self.settings.jam_session_channel_id)
                if isinstance(configured_channel, discord.abc.Messageable):
                    target_channel = configured_channel

            await target_channel.send(f"Session URL: {url}")

        @self.command(name="playlist")
        async def playlist(ctx: commands.Context, url: str) -> None:
            if ctx.guild is None or not isinstance(ctx.author, discord.Member):
                await ctx.send("This command can only be used in a server.")
                return

            if not _ctx_has_mod_access(ctx):
                await ctx.send("You don't have permission to use this command.")
                return

            if ctx.voice_client is None:
                await ctx.send("Use ;join first.")
                return

            session = self._get_session(ctx).for_guild(ctx.guild.id)
            session.now_playing_channel_id = ctx.channel.id

            if not session.submissions_open and not _ctx_has_mod_access(ctx):
                await ctx.send("Submissions are closed.")
                return

            if "https://suno.com/playlist/" not in url:
                await ctx.send("Please provide a Suno playlist URL like https://suno.com/playlist/....")
                return

            session.submissions_open = False
            session.queue.clear()

            await ctx.send("Cleared the queue and closed submissions. Fetching playlist and queuing tracks...")

            try:
                playlist_data = await self.deps.playlist_client.fetch_playlist(url)
            except SunoScrapeError as exc:
                await ctx.send(f"Failed to fetch playlist: {exc}")
                return

            if not playlist_data.items:
                await ctx.send("No songs were found in that playlist.")
                return

            user_id = ctx.author.id
            if session.per_user_limit is not None and not _ctx_has_mod_access(ctx):
                current = session.per_user_counts.get(user_id, 0)
                if current + len(playlist_data.items) > session.per_user_limit:
                    await ctx.send("You have reached the submission limit for this session.")
                    return

            for item in playlist_data.items:
                display_url = item.suno_track_url or item.mp3_url
                track_title = display_url
                audio_url = item.mp3_url
                page_url = item.suno_track_url
                artist_display = None
                media_url = None
                opus_url = None
                track_id: UUID | None = None

                ingest_url = item.suno_track_url or item.mp3_url
                if ingest_url is not None:
                    try:
                        ingest_result = await self.deps.ingest_use_case.execute(
                            IngestSunoLinkInput(
                                guild_id=ctx.guild.id,
                                channel_id=ctx.channel.id,
                                message_id=ctx.message.id,
                                author_id=ctx.author.id,
                                suno_url=ingest_url,
                            )
                        )
                    except SunoScrapeError as exc:
                        logging.warning("Failed to ingest Suno URL %s: %s", ingest_url, exc)
                    else:
                        if ingest_result.track_title:
                            track_title = ingest_result.track_title
                        if ingest_result.mp3_url:
                            audio_url = ingest_result.mp3_url
                        page_url = ingest_result.suno_url
                        artist_display = ingest_result.artist_display
                        media_url = ingest_result.media_url
                        opus_url = self._build_opus_url(ingest_result.track_id)
                        track_id = ingest_result.track_id

                track = Track(
                    audio_url=audio_url,
                    opus_url=opus_url,
                    page_url=page_url,
                    title=track_title,
                    artist_display=artist_display,
                    media_url=media_url,
                    requester_id=ctx.author.id,
                    requester_name=ctx.author.display_name,
                )
                session.queue.append(track)
                session.per_user_counts[user_id] = session.per_user_counts.get(user_id, 0) + 1
                if track_id is not None:
                    asyncio.create_task(self._prefetch_opus(track_id))

            await ctx.send(
                "Queued "
                f"{len(playlist_data.items)} track(s) from the playlist. Submissions are now closed."
            )

            if session.autoplay_enabled and session.now_playing is None and ctx.voice_client is not None:
                started = await self._get_voice(ctx).play_next(ctx.guild)
                if started is not None:
                    session.now_playing_channel_id = ctx.channel.id
                    embed = build_now_playing_embed(started)
                    await ctx.send(embed=embed)

        @self.command(name="playlist-dl")
        async def playlist_download(ctx: commands.Context, url: str) -> None:
            if ctx.guild is None or not isinstance(ctx.author, discord.Member):
                await ctx.send("This command can only be used in a server.")
                return

            if not _ctx_is_master_user(ctx):
                await ctx.send("You don't have permission to use this command.")
                return

            if "https://suno.com/playlist/" not in url:
                await ctx.send("Please provide a Suno playlist URL like https://suno.com/playlist/....")
                return

            await ctx.send("Fetching playlist and building your zip...")

            try:
                playlist_data = await self.deps.playlist_client.fetch_playlist(url)
            except SunoScrapeError as exc:
                await ctx.send(f"Failed to fetch playlist: {exc}")
                return

            if not playlist_data.items:
                await ctx.send("No songs were found in that playlist.")
                return

            try:
                result = await self._deliver_playlist_download(
                    member=ctx.author,
                    guild_name=ctx.guild.name,
                    playlist_url=playlist_data.playlist_url,
                    items=playlist_data.items,
                    filesize_limit=ctx.filesize_limit,
                )
            except RuntimeError as exc:
                await ctx.send(str(exc))
                return
            except discord.Forbidden:
                await ctx.send("I couldn't DM you the playlist download. Please enable DMs and try again.")
                return
            except discord.HTTPException as exc:
                await ctx.send(f"Discord couldn't send the playlist download right now: {exc}")
                return
            except Exception as exc:
                logging.exception("Failed to build playlist archive for %s", playlist_data.playlist_url)
                await ctx.send(f"Failed to build the playlist zip: {type(exc).__name__}: {exc}")
                return

            skipped_note = ""
            if result.skipped_count:
                skipped_note = (
                    f" Downloaded {result.added_count}/{len(playlist_data.items)} track(s); "
                    f"skipped {result.skipped_count}."
                )

            if result.mode == "link":
                success_message = f"Sent a playlist download link to your DMs.{skipped_note}"
            elif result.mode == "attachment":
                success_message = f"Sent the playlist zip to your DMs.{skipped_note}"
            else:
                success_message = f"Sent {result.part_count} archive part(s) to your DMs.{skipped_note}"

            await ctx.send(success_message)

        @self.command(name="q")
        async def queue(ctx: commands.Context) -> None:
            if ctx.guild is None:
                await ctx.send("This command can only be used in a server.")
                return

            session = self._get_session(ctx).for_guild(ctx.guild.id)
            lines: list[str] = []
            if session.submissions_open:
                lines.append("Session is open.")
                if _ctx_has_mod_access(ctx):
                    lines.append("Add a Suno URL to queue a song, or use `;playlist <url>`.")
                else:
                    lines.append("Add a Suno URL to queue a song.")
            else:
                lines.append("Session is closed.")

            if session.queue:
                total = len(session.queue)
                if total == 1:
                    lines.append("Last song")
                elif total > 5:
                    lines.append(f"Next 5 out of {total}")
                else:
                    lines.append(f"Next {total}")
                for idx, track in enumerate(session.queue[:5], start=1):
                    lines.append(f"{idx}. {track.title} (requested by {track.requester_name})")
            else:
                lines.append("Queue is empty.")

            await ctx.send("\n".join(lines))

        @self.command(name="np")
        async def now_playing(ctx: commands.Context) -> None:
            if ctx.guild is None:
                await ctx.send("This command can only be used in a server.")
                return

            session = self._get_session(ctx).for_guild(ctx.guild.id)
            if session.now_playing is None:
                await ctx.send("Nothing is playing.")
                return

            embed = build_now_playing_embed(
                session.now_playing,
                started_at=session.now_playing_started_at,
            )
            await ctx.send(embed=embed)

        @self.command(name="p")
        async def play(ctx: commands.Context) -> None:
            if ctx.guild is None:
                await ctx.send("This command can only be used in a server.")
                return

            session = self._get_session(ctx).for_guild(ctx.guild.id)
            session.now_playing_channel_id = ctx.channel.id
            if session.now_playing is not None:
                await ctx.send(f"Already playing: {session.now_playing.title}. Use ;n to skip.")
                return

            if not session.queue:
                if _ctx_has_mod_access(ctx):
                    await ctx.send(
                        "Queue is empty. Drop a Suno URL or use ;playlist <Suno Playlist URL>."
                    )
                else:
                    await ctx.send("Queue is empty. Drop a Suno URL.")
                return

            try:
                started = await self._get_voice(ctx).play_next(ctx.guild)
            except RuntimeError as exc:
                await ctx.send(
                    "Could not start playback because the voice session was not established. "
                    "Try `;leave`, then `;join`, then `;p` again."
                )
                logging.warning("Play command failed due to voice connection state: %s", exc)
                return
            if started is None:
                await ctx.send("Could not start playback for the next track. Use `;q` to verify queue state.")
                return

            session.now_playing_channel_id = ctx.channel.id
            embed = build_now_playing_embed(
                started,
                started_at=session.now_playing_started_at,
            )
            await ctx.send(embed=embed)

        @self.command(name="n")
        async def skip(ctx: commands.Context) -> None:
            if ctx.guild is None or not isinstance(ctx.author, discord.Member):
                await ctx.send("This command can only be used in a server.")
                return

            if not _ctx_has_mod_access(ctx):
                await ctx.send("You don't have permission to use this command.")
                return

            if ctx.voice_client is None:
                await ctx.send("I'm not connected to a voice channel.")
                return

            session = self._get_session(ctx).for_guild(ctx.guild.id)
            started = await self._get_voice(ctx).skip(ctx.guild)
            if started is None:
                await ctx.send("Skipped. Queue is now empty; playback stopped.")
                return

            session.now_playing_channel_id = ctx.channel.id
            embed = build_now_playing_embed(
                started,
                started_at=session.now_playing_started_at,
            )
            await ctx.send(content="Skipped.", embed=embed)

        @self.command(name="s")
        async def stop(ctx: commands.Context) -> None:
            if ctx.guild is None or not isinstance(ctx.author, discord.Member):
                await ctx.send("This command can only be used in a server.")
                return

            if not _ctx_has_mod_access(ctx):
                await ctx.send("You don't have permission to use this command.")
                return

            if ctx.voice_client is None:
                await ctx.send("I'm not connected to a voice channel.")
                return

            await self._get_voice(ctx).stop(ctx.guild)
            await ctx.send("Playback stopped.")

        @self.command(name="clear")
        async def clear(ctx: commands.Context) -> None:
            if ctx.guild is None or not isinstance(ctx.author, discord.Member):
                await ctx.send("This command can only be used in a server.")
                return

            if not _ctx_has_mod_access(ctx):
                await ctx.send("You don't have permission to use this command.")
                return

            session = self._get_session(ctx).for_guild(ctx.guild.id)
            session.queue.clear()
            await ctx.send("Queue cleared.")

        @self.command(name="remove")
        async def remove(ctx: commands.Context, index: int) -> None:
            if ctx.guild is None or not isinstance(ctx.author, discord.Member):
                await ctx.send("This command can only be used in a server.")
                return

            if not _ctx_has_mod_access(ctx):
                await ctx.send("You don't have permission to use this command.")
                return

            session = self._get_session(ctx).for_guild(ctx.guild.id)
            if index < 1 or index > len(session.queue):
                await ctx.send("Invalid queue index.")
                return

            track = session.queue.pop(index - 1)
            await ctx.send(f"Removed: {track.title} (requested by {track.requester_name}).")

        @self.command(name="limit")
        async def limit(ctx: commands.Context, limit_value: int) -> None:
            if ctx.guild is None or not isinstance(ctx.author, discord.Member):
                await ctx.send("This command can only be used in a server.")
                return

            if not _ctx_has_mod_access(ctx):
                await ctx.send("You don't have permission to use this command.")
                return

            if limit_value < 1:
                await ctx.send("Limit must be at least 1.")
                return

            session = self._get_session(ctx).for_guild(ctx.guild.id)
            session.per_user_limit = limit_value
            await ctx.send(f"Per-user submission limit set to {limit_value}.")

        @self.command(name="autoplay")
        async def autoplay(ctx: commands.Context, value: Optional[str] = None) -> None:
            if ctx.guild is None or not isinstance(ctx.author, discord.Member):
                await ctx.send("This command can only be used in a server.")
                return

            if not _ctx_has_mod_access(ctx):
                await ctx.send("You don't have permission to use this command.")
                return

            session = self._get_session(ctx).for_guild(ctx.guild.id)

            if value is None:
                session.now_playing_channel_id = ctx.channel.id
                session.set_autoplay(None)
                await ctx.send("Autoplay enabled until queue is empty.")
                return

            if value.lower() == "off":
                session.disable_autoplay()
                await ctx.send("Autoplay disabled.")
                return

            try:
                remaining = int(value)
            except ValueError:
                await ctx.send("Autoplay value must be a number or 'off'.")
                return

            if remaining < 1:
                await ctx.send("Autoplay count must be at least 1.")
                return

            session.now_playing_channel_id = ctx.channel.id
            session.set_autoplay(remaining)
            await ctx.send(f"Autoplay enabled for the next {remaining} track(s).")

        @self.command(name="cooldown")
        async def cooldown(ctx: commands.Context, value: Optional[str] = None) -> None:
            if ctx.guild is None or not isinstance(ctx.author, discord.Member):
                await ctx.send("This command can only be used in a server.")
                return

            if not _ctx_has_mod_access(ctx):
                await ctx.send("You don't have permission to use this command.")
                return

            session = self._get_session(ctx).for_guild(ctx.guild.id)

            if value is None or value.lower() == "on":
                session.submission_cooldown_seconds = 15 * 60
                await ctx.send("Submission cooldown set to 15 minutes.")
                return

            if value.lower() == "off":
                session.submission_cooldown_seconds = 0
                await ctx.send("⚠️ Submission cooldown has been deactivated.")
                return

            try:
                minutes = int(value)
            except ValueError:
                await ctx.send("Cooldown value must be a number of minutes or 'off'.")
                return

            if minutes < 1:
                await ctx.send("Cooldown minutes must be at least 1.")
                return

            session.submission_cooldown_seconds = minutes * 60
            await ctx.send(f"Submission cooldown set to {minutes} minute(s).")

        @self.command(name="dj")
        async def dj(ctx: commands.Context, value: Optional[str] = None) -> None:
            if ctx.guild is None or not isinstance(ctx.author, discord.Member):
                await ctx.send("This command can only be used in a server.")
                return

            if not _ctx_has_mod_access(ctx):
                await ctx.send("You don't have permission to use this command.")
                return

            session = self._get_session(ctx).for_guild(ctx.guild.id)

            if value is None:
                session.now_playing_channel_id = ctx.channel.id
                session.set_dj(None)
                await ctx.send("DJ mode enabled until queue is empty.")
                return

            if value.lower() == "off":
                session.disable_dj()
                await ctx.send("DJ mode disabled.")
                return

            try:
                remaining = int(value)
            except ValueError:
                await ctx.send("DJ value must be a number or 'off'.")
                return

            if remaining < 1:
                await ctx.send("DJ count must be at least 1.")
                return

            session.now_playing_channel_id = ctx.channel.id
            session.set_dj(remaining)
            await ctx.send(f"DJ mode enabled for the next {remaining} track(s).")


def build_bot() -> JukeBot:
    """
    Construct the bot with all dependencies wired.
    Keeps global scope clean and avoids import-time side effects.
    """
    settings = load_bot_settings()
    settings.validate_startup()

    intents = discord.Intents.default()
    intents.message_content = True  # required for prefix commands

    session_manager = SessionManager()
    audio_manager = AudioControllerManager()

    track_repo = PostgresTrackRepository(async_session_factory)
    suno_client = HttpxSunoClient()
    deps = BotDeps(
        session_manager=session_manager,
        audio_manager=audio_manager,
        ingest_use_case=IngestSunoLink(
            suno_client=suno_client,
            track_repo=track_repo,
            submission_repo=PostgresSubmissionRepository(async_session_factory),
            queue_repo=PostgresQueueRepository(async_session_factory),
        ),
        suno_client=suno_client,
        playlist_client=HttpxSunoPlaylistClient(),
        submission_repo=PostgresSubmissionRepository(async_session_factory),
        queue_repo=PostgresQueueRepository(async_session_factory),
        track_repo=track_repo,
        voice_service=VoiceOrchestrationService(
            session_manager=session_manager,
            audio_manager=audio_manager,
        ),
        lavalink_client=None,
    )

    return JukeBot(
        settings=settings,
        deps=deps,
        command_prefix=";",
        intents=intents,
    )


def main() -> None:
    """Process entrypoint."""
    bot = build_bot()
    bot.run(bot.settings.active_discord_token)


if __name__ == "__main__":
    main()
