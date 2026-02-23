# apps/bot/jukebotx_bot/main.py
from __future__ import annotations


from dataclasses import dataclass
from datetime import datetime, timezone
import csv
import contextlib
import logging
import math
import os
import re
import tempfile
import asyncio
from typing import Optional
from uuid import UUID

import discord
from discord.ext import commands
import httpx

from jukebotx_bot.discord.audio import AudioControllerManager
from jukebotx_bot.discord.now_playing import build_now_playing_embed
from jukebotx_bot.discord.session import (
    CooldownMode,
    ScrapeFailureEntry,
    SessionManager,
    SessionState,
    Track,
)
from jukebotx_bot.discord.suno import extract_suno_urls
from jukebotx_bot.logging_config import configure_logging, get_event_logger
from jukebotx_bot.settings import load_bot_settings
from jukebotx_core.use_cases.ingest_suno_links import (
    IngestSunoLink,
    IngestSunoLinkInput,
)
from jukebotx_infra.db import async_session_factory, init_db
from jukebotx_infra.repos.queue_repo import PostgresQueueRepository
from jukebotx_infra.repos.submission_repo import PostgresSubmissionRepository
from jukebotx_infra.repos.track_repo import PostgresTrackRepository
from jukebotx_infra.suno.client import SunoScrapeError
from jukebotx_infra.suno.fallback_client import FallbackSunoClient
from jukebotx_infra.suno.playlist_client import HttpxSunoPlaylistClient


AUTO_LEAVE_POLL_SECONDS = float(os.getenv("AUTO_LEAVE_POLL_SECONDS", "30"))
AUTO_LEAVE_IDLE_SECONDS = float(os.getenv("AUTO_LEAVE_IDLE_SECONDS", "600"))
AUTO_LEAVE_SOLO_SECONDS = float(os.getenv("AUTO_LEAVE_SOLO_SECONDS", "120"))

logger = logging.getLogger(__name__)


def _is_mod(member: discord.Member) -> bool:
    """Return True if the member has moderation permissions or an allowed role."""
    perms = member.guild_permissions
    if perms.administrator or perms.manage_guild:
        return True

    allowed_roles = {"admin", "mod", "master of ceremonies", "dj"}
    return any(role.name.lower() in allowed_roles for role in member.roles)


@dataclass(frozen=True)
class BotDeps:
    """
    Dependencies for the bot.
    Keeping these in one object makes lifecycle + testing much saner.
    """

    session_manager: SessionManager
    ingest_use_case: IngestSunoLink
    audio_manager: AudioControllerManager
    playlist_client: HttpxSunoPlaylistClient
    submission_repo: PostgresSubmissionRepository
    queue_repo: PostgresQueueRepository


@dataclass
class StreamRecord:
    guild_id: int
    voice_channel_id: int
    owner_user_id: int
    created_at: datetime
    status: str = "active"


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
        self._streams: dict[int, list[StreamRecord]] = {}
        self._auto_leave_task: asyncio.Task[None] | None = None

        self.remove_command("help")

        # Register events + commands once, right after construction.
        self._register_events()
        self._register_commands()

    async def setup_hook(self) -> None:
        """
        discord.py v2.x startup hook.
        Runs once, before on_ready, after the bot connects.
        """
        await init_db()
        self._log_canonical_event(
            event_name="db_init_complete",
            guild_id=None,
            channel_id=None,
            user_id=None,
            trigger="setup_hook",
        )

        # If you later convert cogs to extensions, load them here:
        # await self.load_extension("jukebotx_bot.discord.cogs.queue")
        # await self.load_extension("jukebotx_bot.discord.cogs.config")

        if self._auto_leave_task is None:
            self._auto_leave_task = asyncio.create_task(self._auto_leave_loop())

    async def close(self) -> None:
        self._log_canonical_event(
            event_name="bot_shutdown",
            guild_id=None,
            channel_id=None,
            user_id=getattr(self.user, "id", None),
            trigger="bot_close",
        )
        task = self._auto_leave_task
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            self._auto_leave_task = None
        await super().close()

    # -----------------------------
    # Internal helpers
    # -----------------------------
    def _get_session(self, ctx: commands.Context) -> SessionManager:
        return self.deps.session_manager

    def _get_audio(self, ctx: commands.Context) -> AudioControllerManager:
        return self.deps.audio_manager

    def _log_canonical_event(
        self,
        *,
        event_name: str,
        guild_id: int | None,
        channel_id: int | None,
        user_id: int | None,
        trigger: str,
        **context: object,
    ) -> None:
        get_event_logger(
            logger,
            event_name=event_name,
            guild_id=guild_id,
            channel_id=channel_id,
            user_id=user_id,
            trigger=trigger,
            **context,
        ).info("Canonical event emitted: %s", event_name)

    def _upsert_stream(self, stream: StreamRecord) -> StreamRecord:
        guild_streams = self._streams.setdefault(stream.guild_id, [])
        for idx, existing in enumerate(guild_streams):
            if existing.voice_channel_id == stream.voice_channel_id:
                guild_streams[idx] = stream
                return stream
        guild_streams.append(stream)
        return stream

    def _remove_active_stream(self, *, guild_id: int, voice_channel_id: int) -> None:
        guild_streams = self._streams.get(guild_id, [])
        for existing in guild_streams:
            if existing.voice_channel_id == voice_channel_id and existing.status == "active":
                existing.status = "ended"
                break
        self._streams[guild_id] = [
            existing
            for existing in guild_streams
            if existing.status == "active"
        ]

    async def _teardown_voice_session(
        self,
        *,
        guild: discord.Guild,
        voice_channel_id: int,
        voice_client: discord.VoiceClient | None,
        reason: str,
        channel_id_to_clear: int | None = None,
        moderator_channel: discord.abc.Messageable | None = None,
    ) -> None:
        session = self.deps.session_manager.for_guild(guild.id)
        await self._send_scrape_failure_report(
            guild_id=guild.id,
            session=session,
            moderator_channel=moderator_channel,
            reason=f"closure summary: {reason}",
            force=True,
        )
        session.queue.clear()
        session.scrape_failures.clear()
        session.scrape_alerts.pending_failures.clear()
        session.scrape_alerts.recent_failure_timestamps.clear()
        session.scrape_alerts.fingerprint_timestamps.clear()
        session.scrape_alerts.consecutive_failures = 0
        session.scrape_alerts.last_failure_fingerprint = None
        session.now_playing_channel_id = None
        session.stop_playback()

        if voice_client is not None:
            audio = self.deps.audio_manager.for_guild(guild.id, session)
            await audio.stop(voice_client)
            await voice_client.disconnect()

        self._remove_active_stream(guild_id=guild.id, voice_channel_id=voice_channel_id)

        await self.deps.queue_repo.clear(guild_id=guild.id)
        if channel_id_to_clear is not None:
            await self.deps.submission_repo.clear_for_channel(
                guild_id=guild.id,
                channel_id=channel_id_to_clear,
            )

        get_event_logger(
            logger,
            event_name="voice_session_ended",
            guild_id=guild.id,
            channel_id=voice_channel_id,
        ).info(
            "Ended voice session for guild %s voice_channel %s (%s)",
            guild.id,
            voice_channel_id,
            reason,
        )
        self._log_canonical_event(
            event_name="session_reset",
            guild_id=guild.id,
            channel_id=voice_channel_id,
            user_id=None,
            trigger=reason,
        )

    def _should_auto_leave(
        self,
        *,
        session: SessionState,
        stream: StreamRecord,
        voice_client: discord.VoiceClient,
        now_epoch: float,
        now_monotonic: float,
    ) -> str | None:
        channel = getattr(voice_client, "channel", None)
        if channel is None:
            return None

        members = getattr(channel, "members", [])
        human_members = [member for member in members if not getattr(member, "bot", False)]
        stream_age = now_epoch - stream.created_at.timestamp()

        if session.submissions_open and not human_members and stream_age >= AUTO_LEAVE_SOLO_SECONDS:
            return "bot alone in voice channel"

        playback_idle_seconds = now_monotonic - session.last_playback_event_at
        if session.now_playing is None and not session.queue and playback_idle_seconds >= AUTO_LEAVE_IDLE_SECONDS:
            return "queue empty and playback idle"

        return None

    async def _auto_leave_loop(self) -> None:
        while True:
            await asyncio.sleep(AUTO_LEAVE_POLL_SECONDS)
            try:
                await self._run_auto_leave_check()
            except Exception as exc:  # pragma: no cover - defensive
                get_event_logger(
                    logger,
                    event_name="auto_leave_check_failed",
                    error_type=type(exc).__name__,
                ).warning("Auto-leave check failed: %s", exc)

    async def _run_auto_leave_check(self) -> None:
        now_epoch = datetime.now(timezone.utc).timestamp()
        now_monotonic = asyncio.get_running_loop().time()
        for voice_client in list(self.voice_clients):
            guild = voice_client.guild
            if guild is None:
                continue

            guild_streams = [
                stream
                for stream in self._streams.get(guild.id, [])
                if stream.status == "active"
            ]
            if not guild_streams:
                continue

            stream = next(
                (
                    item
                    for item in guild_streams
                    if getattr(voice_client.channel, "id", None) == item.voice_channel_id
                ),
                None,
            )
            if stream is None:
                continue

            session = self.deps.session_manager.for_guild(guild.id)
            reason = self._should_auto_leave(
                session=session,
                stream=stream,
                voice_client=voice_client,
                now_epoch=now_epoch,
                now_monotonic=now_monotonic,
            )
            if reason is None:
                continue

            self._log_canonical_event(
                event_name="auto_leave_triggered",
                guild_id=guild.id,
                channel_id=stream.voice_channel_id,
                user_id=stream.owner_user_id,
                trigger="auto_leave_loop",
                reason=reason,
            )

            announce_channel_id = session.now_playing_channel_id
            await self._teardown_voice_session(
                guild=guild,
                voice_channel_id=stream.voice_channel_id,
                voice_client=voice_client,
                reason=f"auto leave: {reason}",
            )

            if announce_channel_id is not None:
                announce_channel = guild.get_channel(announce_channel_id)
                can_send = announce_channel is not None and callable(getattr(announce_channel, "send", None))
                if can_send:
                    await announce_channel.send(
                        "Auto-left the voice channel: queue idle timeout or bot was alone in VC."
                    )

    async def _resolve_active_stream(
        self,
        ctx: commands.Context,
        *,
        require_author_in_stream_vc: bool = True,
    ) -> StreamRecord | None:
        if ctx.guild is None or not isinstance(ctx.author, discord.Member):
            await ctx.send("This command can only be used in a server.")
            return None

        guild_streams = [
            stream
            for stream in self._streams.get(ctx.guild.id, [])
            if stream.status == "active"
        ]
        if not guild_streams:
            await ctx.send("No active stream found. Use ;join first.")
            return None

        author_voice_channel_id = (
            ctx.author.voice.channel.id
            if ctx.author.voice is not None and ctx.author.voice.channel is not None
            else None
        )

        active_stream = None
        if author_voice_channel_id is not None:
            active_stream = next(
                (
                    stream
                    for stream in guild_streams
                    if stream.voice_channel_id == author_voice_channel_id
                ),
                None,
            )

        if active_stream is None:
            active_stream = next(
                (stream for stream in guild_streams if stream.owner_user_id == ctx.author.id),
                None,
            )

        if active_stream is None:
            await ctx.send(
                "Couldn't resolve an active stream for you. Join the stream VC or use ;join."
            )
            return None

        if require_author_in_stream_vc and author_voice_channel_id != active_stream.voice_channel_id:
            await ctx.send(
                "This command only works from the stream's voice channel context. Join the active stream VC first."
            )
            return None

        return active_stream

    async def _resolve_stream_session(
        self, ctx: commands.Context, *, require_author_in_stream_vc: bool = True
    ) -> tuple[StreamRecord, SessionState] | None:
        stream = await self._resolve_active_stream(
            ctx,
            require_author_in_stream_vc=require_author_in_stream_vc,
        )
        if stream is None:
            return None
        session = self._get_session(ctx).for_guild(stream.guild_id)
        return stream, session

    def _build_opus_url(self, track_id: UUID | None) -> str | None:
        if track_id is None or self.settings.opus_api_base_url is None:
            return None
        base_url = self.settings.opus_api_base_url.rstrip("/")
        return f"{base_url}/tracks/{track_id}/opus"

    async def _prefetch_opus(self, track_id: UUID) -> None:
        if self.settings.opus_api_base_url is None:
            return
        status_url = f"{self.settings.opus_api_base_url.rstrip('/')}/tracks/{track_id}/opus/status"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.get(status_url)
        except Exception as exc:
            get_event_logger(
                logger,
                event_name="opus_prefetch_failed",
                error_type=type(exc).__name__,
            ).warning("Failed to prefetch opus status for %s: %s", track_id, exc)

    def _session_limit_reached(self, session) -> bool:
        return (
            session.session_total_limit is not None
            and session.total_tracks_added >= session.session_total_limit
        )

    def _record_track_addition(self, session, requester_id: int) -> None:
        session.per_user_counts[requester_id] = (
            session.per_user_counts.get(requester_id, 0) + 1
        )
        session.total_tracks_added += 1

    def _build_failure_fingerprint(self, *, url: str, error_summary: str) -> str:
        parsed = httpx.URL(url)
        domain_and_path = f"{parsed.host or 'unknown'}{parsed.path or '/'}".lower()
        return f"{error_summary.strip().lower()}||{domain_and_path}"

    def _dedupe_failures(self, failures: list[ScrapeFailureEntry]) -> list[ScrapeFailureEntry]:
        deduped: dict[str, ScrapeFailureEntry] = {}
        for failure in failures:
            key = self._build_failure_fingerprint(
                url=failure.url,
                error_summary=failure.error_summary,
            )
            if key not in deduped:
                deduped[key] = failure
        return list(deduped.values())

    def _record_scrape_failure(
        self,
        *,
        session,
        guild_id: int,
        channel_id: int,
        message_id: int,
        url: str,
        error_summary: str,
        fallback_attempted: bool = False,
    ) -> None:
        failure_entry = ScrapeFailureEntry(
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
            url=url,
            error_summary=error_summary,
            guild_id=guild_id,
            channel_id=channel_id,
            message_id=message_id,
            fallback_attempted=fallback_attempted,
        )
        session.scrape_failures.append(failure_entry)

        alerts = session.scrape_alerts
        now_monotonic = asyncio.get_running_loop().time()
        fingerprint = self._build_failure_fingerprint(url=url, error_summary=error_summary)

        alerts.pending_failures.append(failure_entry)
        alerts.recent_failure_timestamps.append(now_monotonic)
        burst_window = max(1, self.settings.master_dm_burst_window_seconds)
        alerts.recent_failure_timestamps = [
            ts for ts in alerts.recent_failure_timestamps if (now_monotonic - ts) <= burst_window
        ]

        fingerprint_window = 5 * 60
        fingerprint_events = alerts.fingerprint_timestamps.setdefault(fingerprint, [])
        fingerprint_events.append(now_monotonic)
        alerts.fingerprint_timestamps[fingerprint] = [
            ts for ts in fingerprint_events if (now_monotonic - ts) <= fingerprint_window
        ]

        if alerts.last_failure_fingerprint == fingerprint:
            alerts.consecutive_failures += 1
        else:
            alerts.last_failure_fingerprint = fingerprint
            alerts.consecutive_failures = 1

    def _mark_scrape_success(self, *, session: SessionState) -> None:
        alerts = session.scrape_alerts
        alerts.consecutive_failures = 0
        alerts.last_failure_fingerprint = None

    async def _send_scrape_failure_report(
        self,
        *,
        guild_id: int,
        session,
        moderator_channel: discord.abc.Messageable | None = None,
        reason: str,
        force: bool = False,
    ) -> None:
        alerts = session.scrape_alerts
        if not alerts.pending_failures:
            return

        now_monotonic = asyncio.get_running_loop().time()
        min_interval = max(1, self.settings.master_dm_min_interval_seconds)
        burst_threshold = max(1, self.settings.master_dm_burst_threshold)

        recent_failure_count = len(alerts.recent_failure_timestamps)
        repeated_fingerprint_count = 0
        if alerts.last_failure_fingerprint is not None:
            repeated_fingerprint_count = len(
                alerts.fingerprint_timestamps.get(alerts.last_failure_fingerprint, [])
            )

        high_severity = (
            recent_failure_count >= burst_threshold
            or repeated_fingerprint_count >= 3
        )
        periodic_due = (
            alerts.last_dm_sent_at is None
            or (now_monotonic - alerts.last_dm_sent_at) >= min_interval
        )

        if not force and not high_severity and not periodic_due:
            return

        pending_failures = list(alerts.pending_failures)
        deduped_failures = self._dedupe_failures(pending_failures)

        master_user_id = self.settings.master_user_id
        if master_user_id is None:
            get_event_logger(
                logger,
                event_name="scrape_report_skipped",
                guild_id=guild_id,
            ).info(
                "Skipping scrape failure report for guild_id=%s: MASTER_USER_ID is not configured.",
                guild_id,
            )
            return

        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix=".txt") as tmp_file:
            tmp_file.write(
                f"JukeBotx scrape failure report\nGuild ID: {guild_id}\nReason: {reason}\nGenerated UTC: {datetime.now(timezone.utc).isoformat()}\nTotal failures in digest: {len(deduped_failures)}\n\n"
            )
            for idx, failure in enumerate(deduped_failures, start=1):
                tmp_file.write(
                    f"[{idx}] timestamp_utc={failure.timestamp_utc}\n"
                    f"    url={failure.url}\n"
                    f"    error_summary={failure.error_summary}\n"
                    f"    guild_id={failure.guild_id}\n"
                    f"    channel_id={failure.channel_id}\n"
                    f"    message_id={failure.message_id}\n"
                    f"    fallback_attempted={failure.fallback_attempted}\n\n"
                )
            tmp_path = tmp_file.name

        try:
            user = await self.fetch_user(master_user_id)
            await user.send(
                content=(
                    f"Scrape failure report for guild {guild_id}. Trigger: {reason}."
                ),
                file=discord.File(tmp_path, filename=f"scrape_failures_{guild_id}.txt"),
            )
            alerts.pending_failures.clear()
            alerts.last_dm_sent_at = now_monotonic
        except discord.NotFound:
            get_event_logger(
                logger,
                event_name="scrape_report_master_user_not_found",
                guild_id=guild_id,
                user_id=master_user_id,
                error_type="NotFound",
            ).warning(
                "MASTER_USER_ID user not found for scrape failure report (master_user_id=%s guild_id=%s)",
                master_user_id,
                guild_id,
            )
            if (
                moderator_channel is not None
                and (
                    alerts.last_channel_warning_at is None
                    or (now_monotonic - alerts.last_channel_warning_at) >= min_interval
                )
            ):
                try:
                    await moderator_channel.send(
                        "⚠️ Could not deliver scrape failure report (master user not found)."
                    )
                    alerts.last_channel_warning_at = now_monotonic
                except discord.HTTPException:
                    pass
        except discord.Forbidden:
            get_event_logger(
                logger,
                event_name="scrape_report_dm_forbidden",
                guild_id=guild_id,
                user_id=master_user_id,
                error_type="Forbidden",
            ).warning(
                "DM forbidden while sending scrape failure report to master user (master_user_id=%s guild_id=%s)",
                master_user_id,
                guild_id,
            )
            if (
                moderator_channel is not None
                and (
                    alerts.last_channel_warning_at is None
                    or (now_monotonic - alerts.last_channel_warning_at) >= min_interval
                )
            ):
                try:
                    await moderator_channel.send(
                        "⚠️ Could not DM scrape failure report to MASTER_USER_ID."
                    )
                    alerts.last_channel_warning_at = now_monotonic
                except discord.HTTPException:
                    pass
        except discord.HTTPException as exc:
            get_event_logger(
                logger,
                event_name="scrape_report_http_error",
                guild_id=guild_id,
                user_id=master_user_id,
                error_type=type(exc).__name__,
            ).warning(
                "HTTP error while sending scrape failure report (master_user_id=%s guild_id=%s): %s",
                master_user_id,
                guild_id,
                exc,
            )
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                get_event_logger(
                    logger,
                    event_name="scrape_report_tempfile_delete_failed",
                    guild_id=guild_id,
                    error_type="OSError",
                ).warning("Failed to delete temp scrape report file: %s", tmp_path)

    # -----------------------------
    # Events
    # -----------------------------
    def _register_events(self) -> None:
        async def _send_submission_feedback(
            message: discord.Message, content: str
        ) -> None:
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
        async def on_command_error(
            ctx: commands.Context, error: commands.CommandError
        ) -> None:
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
            assert (
                self.user is not None
            ), "client.user is unexpectedly None in on_ready()"

            bot_name = self.user.name.lower().strip()
            env = self.settings.env.lower().strip()

            # Production safety: prevent using a dev bot identity with production settings.
            assert env != "production" or "dev" not in bot_name, (
                "Safety check failed: ENV=production but the connected Discord bot name "
                "contains 'dev'. You are likely using the DEV bot token in production."
            )

            # Development safety: prevent using prod bot identity in development.
            assert env != "development" or "dev" in bot_name, (
                "Safety check failed: ENV=development but the connected Discord bot name "
                "does NOT contain 'dev'. You are likely using the production bot token in development."
            )

            get_event_logger(logger, event_name="discord_ready").info(
                "Connected as %s (env=%s)", self.user, self.settings.env
            )
            self._log_canonical_event(
                event_name="bot_ready",
                guild_id=None,
                channel_id=None,
                user_id=self.user.id,
                trigger="on_ready",
                env=self.settings.env,
            )

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
            url_outcomes: dict[str, dict[str, str]] = {}
            success_count = 0
            failure_count = 0

            session = self.deps.session_manager.for_guild(message.guild.id)
            is_host = isinstance(message.author, discord.Member) and _is_mod(
                message.author
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
                    if blocked_reason is None and self._session_limit_reached(session):
                        session.submissions_open = False
                        blocked_reason = (
                            "Session closed at limit: no more tracks can be added."
                        )
                    if blocked_reason is None:
                        if session.cooldown_mode == CooldownMode.QUEUE:
                            if session.has_user_track_in_queue(user_id):
                                blocked_reason = "Queue cooldown is enabled. You already have a track in queue or now playing."
                        else:
                            cooldown_remaining = session.cooldown_remaining(user_id)
                            if cooldown_remaining > 0:
                                blocked_reason = (
                                    "You're on cooldown for another "
                                    f"{math.ceil(cooldown_remaining)}s before submitting again."
                                )
            for url in urls:
                if "https://suno.com/playlist/" in url:
                    skipped_playlist = True
                    url_outcomes[url] = {
                        "status": "skipped_playlist",
                        "reason": "playlist links require ;playlist command",
                    }
                    continue
                if blocked_reason is not None:
                    url_outcomes[url] = {"status": "blocked", "reason": blocked_reason}
                    failure_count += 1
                    continue
                if self._session_limit_reached(session):
                    session.submissions_open = False
                    limit_reached = True
                    url_outcomes[url] = {
                        "status": "blocked",
                        "reason": "session closed at limit",
                    }
                    failure_count += 1
                    break
                if remaining_slots is not None and remaining_slots <= 0:
                    limit_reached = True
                    url_outcomes[url] = {
                        "status": "blocked",
                        "reason": "per-user submission limit reached",
                    }
                    failure_count += 1
                    break
                try:
                    self._log_canonical_event(
                        event_name="ingest_attempt",
                        guild_id=message.guild.id,
                        channel_id=message.channel.id,
                        user_id=message.author.id,
                        trigger="auto_message_ingest",
                        source="message_url",
                        suno_url=url,
                    )
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
                    url_outcomes[url] = {
                        "status": "scrape_failure",
                        "reason": str(exc) or "scrape error",
                    }
                    failure_count += 1
                    get_event_logger(
                        logger,
                        event_name="suno_ingest_scrape_failure",
                        guild_id=message.guild.id,
                        channel_id=message.channel.id,
                        user_id=message.author.id,
                        message_id=message.id,
                        error_type=type(exc).__name__,
                    ).exception(
                        "Suno ingestion scrape failure for url=%s guild_id=%s channel_id=%s message_id=%s",
                        url,
                        message.guild.id,
                        message.channel.id,
                        message.id,
                    )
                    self._record_scrape_failure(
                        session=session,
                        guild_id=message.guild.id,
                        channel_id=message.channel.id,
                        message_id=message.id,
                        url=url,
                        error_summary=str(exc) or "scrape error",
                    )
                    await self._send_scrape_failure_report(
                        guild_id=message.guild.id,
                        session=session,
                        moderator_channel=message.channel,
                        reason="auto-ingest scrape failure",
                    )
                    self._log_canonical_event(
                        event_name="ingest_failure_scrape",
                        guild_id=message.guild.id,
                        channel_id=message.channel.id,
                        user_id=message.author.id,
                        trigger="auto_message_ingest",
                        source="message_url",
                        suno_url=url,
                        error_type=type(exc).__name__,
                    )
                    continue

                if not result.mp3_url:
                    url_outcomes[url] = {
                        "status": "missing_required_fields",
                        "reason": "missing mp3_url",
                    }
                    failure_count += 1
                    get_event_logger(
                        logger,
                        event_name="suno_ingest_missing_required_fields",
                        guild_id=message.guild.id,
                        channel_id=message.channel.id,
                        user_id=message.author.id,
                        message_id=message.id,
                    ).warning(
                        "Suno ingestion missing required fields for url=%s guild_id=%s channel_id=%s message_id=%s",
                        url,
                        message.guild.id,
                        message.channel.id,
                        message.id,
                    )
                    self._record_scrape_failure(
                        session=session,
                        guild_id=message.guild.id,
                        channel_id=message.channel.id,
                        message_id=message.id,
                        url=url,
                        error_summary="missing mp3_url",
                    )
                    await self._send_scrape_failure_report(
                        guild_id=message.guild.id,
                        session=session,
                        moderator_channel=message.channel,
                        reason="auto-ingest validation failure",
                    )
                    self._log_canonical_event(
                        event_name="ingest_failure_validation",
                        guild_id=message.guild.id,
                        channel_id=message.channel.id,
                        user_id=message.author.id,
                        trigger="auto_message_ingest",
                        source="message_url",
                        suno_url=url,
                        validation_error="missing_mp3_url",
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
                self._record_track_addition(session, track.requester_id)
                asyncio.create_task(self._prefetch_opus(result.track_id))
                added_any = True
                success_count += 1
                self._mark_scrape_success(session=session)
                url_outcomes[url] = {"status": "success", "reason": "queued"}
                self._log_canonical_event(
                    event_name="ingest_success",
                    guild_id=message.guild.id,
                    channel_id=message.channel.id,
                    user_id=message.author.id,
                    trigger="auto_message_ingest",
                    source="message_url",
                    suno_url=url,
                    queue_size=len(session.queue),
                )
                if remaining_slots is not None:
                    remaining_slots -= 1

            if added_any:
                session.mark_submission(user_id)
                try:
                    await message.add_reaction("🤘")
                except discord.HTTPException:
                    pass

            if success_count > 0 and failure_count > 0:
                try:
                    await message.channel.send(
                        f"Queued {success_count} link(s); {failure_count} failed (see logs)."
                    )
                except discord.HTTPException:
                    pass

            if not added_any and any(
                outcome.get("status") in {"scrape_failure", "missing_required_fields"}
                for outcome in url_outcomes.values()
            ):
                try:
                    await message.add_reaction("❌")
                except discord.HTTPException:
                    pass

            if blocked_reason is not None:
                await _send_submission_feedback(message, blocked_reason)
            elif limit_reached:
                await _send_submission_feedback(
                    message,
                    "Session closed at limit: no more tracks can be added. Additional songs were not queued.",
                )
                await self._send_scrape_failure_report(
                    guild_id=message.guild.id,
                    session=session,
                    moderator_channel=message.channel,
                    reason="session limit reached from auto-ingest",
                )

            if skipped_playlist:
                await message.channel.send(
                    "Playlist links aren’t auto-ingested. Use `;playlist <url>` instead."
                )

            await self.process_commands(message)

    # -----------------------------
    # Commands
    # -----------------------------
    def _register_commands(self) -> None:
        @self.command(name="help")
        async def help_command(ctx: commands.Context) -> None:
            is_mod = isinstance(ctx.author, discord.Member) and _is_mod(ctx.author)
            embed = discord.Embed(
                title="JukeBotx Help",
                description=(
                    "Command prefix: `;`\n"
                    "Drop Suno links in chat to queue when submissions are open. "
                    "Use `;playlist <url>` for Suno playlists (mods only)."
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
                    "`;pause` — Pause playback (mods).\n"
                    "`;resume` — Resume playback (mods).\n"
                    "`;n` — Skip the current track (mods).\n"
                    "`;s` — Stop playback (mods)."
                ),
                inline=False,
            )
            if is_mod:
                embed.add_field(
                    name="Queue Management (mods)",
                    value=(
                        "`;playlist <url>` — Queue a Suno playlist and close submissions.\n"
                        "`;clear` — Clear the queue.\n"
                        "`;remove <index>` — Remove a queued item.\n"
                        "`;limit <count>` — Set per-user submission limit.\n"
                        "`;limit --session <count>` — Set session-wide total track cap."
                    ),
                    inline=False,
                )
                embed.add_field(
                    name="Autoplay + DJ Mode (mods)",
                    value=(
                        "`;autoplay` — Enable autoplay until the queue ends.\n"
                        "`;autoplay <count>` — Play the next N tracks.\n"
                        "`;autoplay off` — Disable autoplay.\n"
                        "`;cooldown` / `;cooldown <minutes>` / `;cooldown -queue` / `;cooldown off` — Toggle submission cooldown.\n"
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

            if not isinstance(ctx.author, discord.Member) or not _is_mod(ctx.author):
                await ctx.send("You don't have permission to use this command.")
                return

            if ctx.author.voice is None or ctx.author.voice.channel is None:
                await ctx.send("You're not in a voice channel!")
                return

            channel = ctx.author.voice.channel

            try:
                await channel.connect()
            except discord.Forbidden:
                await ctx.send(
                    "🚫 I don't have permission to join that voice channel (View/Connect)."
                )
                return
            except Exception as exc:
                await ctx.send(f"⚠️ Failed to join VC: {type(exc).__name__}: {exc}")
                raise

            self._upsert_stream(
                StreamRecord(
                    guild_id=ctx.guild.id,
                    voice_channel_id=channel.id,
                    owner_user_id=ctx.author.id,
                    created_at=datetime.now(timezone.utc),
                    status="active",
                )
            )
            session = self.deps.session_manager.for_guild(ctx.guild.id)
            session.last_playback_event_at = asyncio.get_running_loop().time()
            await ctx.send(f"Joined {channel.name}! Stream is now active.")
            self._log_canonical_event(
                event_name="session_join",
                guild_id=ctx.guild.id,
                channel_id=channel.id,
                user_id=ctx.author.id,
                trigger=";join",
                source="command",
            )

        @self.command(name="leave")
        async def leave(ctx: commands.Context) -> None:
            if ctx.guild is None:
                await ctx.send("This command can only be used in a server.")
                return

            if not isinstance(ctx.author, discord.Member) or not _is_mod(ctx.author):
                await ctx.send("You don't have permission to use this command.")
                return

            stream_session = await self._resolve_stream_session(ctx)
            if stream_session is None:
                return

            stream, _ = stream_session
            await self._teardown_voice_session(
                guild=ctx.guild,
                voice_channel_id=stream.voice_channel_id,
                voice_client=ctx.voice_client,
                reason="manual leave command",
                channel_id_to_clear=ctx.channel.id,
                moderator_channel=ctx.channel,
            )

            await ctx.send("Left the voice channel. Cleared active stream state for this VC.")
            self._log_canonical_event(
                event_name="session_leave",
                guild_id=ctx.guild.id,
                channel_id=stream.voice_channel_id,
                user_id=ctx.author.id,
                trigger=";leave",
                source="command",
            )

        @self.command(name="setlist")
        async def setlist(ctx: commands.Context) -> None:
            if ctx.guild is None:
                await ctx.send("This command can only be used in a server.")
                return

            if not isinstance(ctx.author, discord.Member) or not _is_mod(ctx.author):
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
            channel_slug = (
                re.sub(r"[^a-z0-9]+", "_", channel_name).strip("_") or "session"
            )
            date_stamp = datetime.now(timezone.utc).strftime("%b_%d_%Y").lower()
            filename = f"{channel_slug}_{date_stamp}.csv"

            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", newline="", delete=False
            ) as tmp_file:
                writer = csv.writer(tmp_file)
                writer.writerow(["Artist", "Title", "URL", "Requested By"])
                for track in tracks:
                    artist = track.artist_display or "Unknown Artist"
                    title = track.title or "Untitled"
                    url = track.suno_url or track.mp3_url or ""
                    requester = ""
                    if track.requester_id is not None:
                        member = ctx.guild.get_member(track.requester_id)
                        requester = (
                            member.display_name
                            if member is not None
                            else str(track.requester_id)
                        )
                    writer.writerow([artist, title, url, requester])
                tmp_path = tmp_file.name

            try:
                await ctx.author.send(
                    content=(
                        "Here's your session setlist CSV!\n"
                        "Google Sheets import:\n"
                        "• Upload CSV\n"
                        "• Use comma delimiter\n"
                        "• Confirm UTF-8 encoding if prompted"
                    ),
                    file=discord.File(tmp_path, filename=filename),
                )
            except discord.Forbidden:
                await ctx.send(
                    "I couldn't DM you the setlist. Please enable DMs and try again."
                )
                return
            finally:
                try:
                    os.remove(tmp_path)
                except OSError:
                    get_event_logger(
                        logger,
                        event_name="setlist_tempfile_delete_failed",
                        guild_id=ctx.guild.id if ctx.guild is not None else None,
                        channel_id=ctx.channel.id,
                        user_id=ctx.author.id,
                        error_type="OSError",
                    ).warning("Failed to delete temp setlist file: %s", tmp_path)

            await ctx.send("Setlist sent via DM.")

        @self.command(name="ping")
        async def ping(ctx: commands.Context, target: str, *, message: str) -> None:
            if ctx.guild is None or not isinstance(ctx.author, discord.Member):
                await ctx.send("This command can only be used in a server.")
                return

            if not _is_mod(ctx.author):
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

            if not isinstance(ctx.author, discord.Member) or not _is_mod(ctx.author):
                await ctx.send("You don't have permission to use this command.")
                return

            stream_session = await self._resolve_stream_session(ctx)
            if stream_session is None:
                return

            _, session = stream_session
            session.submissions_open = True
            session.reset_submission_counts()
            await ctx.send("Submissions are open.")

        @self.command(name="close")
        async def close_submissions(ctx: commands.Context) -> None:
            if ctx.guild is None:
                await ctx.send("This command can only be used in a server.")
                return

            if not isinstance(ctx.author, discord.Member) or not _is_mod(ctx.author):
                await ctx.send("You don't have permission to use this command.")
                return

            stream_session = await self._resolve_stream_session(ctx)
            if stream_session is None:
                return

            _, session = stream_session
            session.submissions_open = False
            await ctx.send("Submissions are closed.")

        @self.command(name="web", aliases=["sessionurl"])
        async def web(ctx: commands.Context) -> None:
            if ctx.guild is None:
                await ctx.send("This command can only be used in a server.")
                return

            if not isinstance(ctx.author, discord.Member) or not _is_mod(ctx.author):
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
                configured_channel = ctx.guild.get_channel(
                    self.settings.jam_session_channel_id
                )
                if isinstance(configured_channel, discord.abc.Messageable):
                    target_channel = configured_channel

            await target_channel.send(f"Session URL: {url}")

        @self.command(name="playlist")
        async def playlist(ctx: commands.Context, url: str) -> None:
            if ctx.guild is None or not isinstance(ctx.author, discord.Member):
                await ctx.send("This command can only be used in a server.")
                return

            if not _is_mod(ctx.author):
                await ctx.send("You don't have permission to use this command.")
                return

            if ctx.voice_client is None:
                await ctx.send("Use ;join first.")
                return

            stream_session = await self._resolve_stream_session(ctx)
            if stream_session is None:
                return

            _, session = stream_session
            session.now_playing_channel_id = ctx.channel.id

            if not session.submissions_open and not _is_mod(ctx.author):
                await ctx.send("Submissions are closed.")
                return

            if "https://suno.com/playlist/" not in url:
                await ctx.send(
                    "Please provide a Suno playlist URL like https://suno.com/playlist/...."
                )
                return

            try:
                playlist_data = await self.deps.playlist_client.fetch_playlist(url)
            except SunoScrapeError as exc:
                await ctx.send(f"Failed to fetch playlist: {exc}")
                return

            if not playlist_data.items:
                await ctx.send("No songs were found in that playlist.")
                return

            user_id = ctx.author.id
            if session.per_user_limit is not None and not _is_mod(ctx.author):
                current = session.per_user_counts.get(user_id, 0)
                if current + len(playlist_data.items) > session.per_user_limit:
                    await ctx.send(
                        "You have reached the submission limit for this session."
                    )
                    return

            if self._session_limit_reached(session):
                session.submissions_open = False
                await ctx.send("Session closed at limit: no more tracks can be added.")
                return

            accepted_count = 0
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
                        self._log_canonical_event(
                            event_name="ingest_attempt",
                            guild_id=ctx.guild.id,
                            channel_id=ctx.channel.id,
                            user_id=ctx.author.id,
                            trigger=";playlist",
                            source="playlist_item",
                            suno_url=ingest_url,
                        )
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
                        get_event_logger(
                            logger,
                            event_name="playlist_ingest_failed",
                            guild_id=ctx.guild.id,
                            channel_id=ctx.channel.id,
                            user_id=ctx.author.id,
                            message_id=ctx.message.id,
                            error_type=type(exc).__name__,
                        ).warning("Failed to ingest Suno URL %s: %s", ingest_url, exc)
                        self._log_canonical_event(
                            event_name="ingest_failure_scrape",
                            guild_id=ctx.guild.id,
                            channel_id=ctx.channel.id,
                            user_id=ctx.author.id,
                            trigger=";playlist",
                            source="playlist_item",
                            suno_url=ingest_url,
                            error_type=type(exc).__name__,
                        )
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
                        self._log_canonical_event(
                            event_name="ingest_success",
                            guild_id=ctx.guild.id,
                            channel_id=ctx.channel.id,
                            user_id=ctx.author.id,
                            trigger=";playlist",
                            source="playlist_item",
                            suno_url=ingest_url,
                        )

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
                if self._session_limit_reached(session):
                    session.submissions_open = False
                    await self._send_scrape_failure_report(
                        guild_id=ctx.guild.id,
                        session=session,
                        moderator_channel=ctx.channel,
                        reason="session closed after setting session limit",
                    )
                    break

                session.queue.append(track)
                self._record_track_addition(session, user_id)
                accepted_count += 1
                if track_id is not None:
                    asyncio.create_task(self._prefetch_opus(track_id))

            if accepted_count == 0:
                session.submissions_open = False
                await self._send_scrape_failure_report(
                    guild_id=ctx.guild.id,
                    session=session,
                    moderator_channel=ctx.channel,
                    reason="session limit reached while queueing playlist",
                )
                await ctx.send("Session closed at limit: no more tracks can be added.")
                return

            session.submissions_open = False
            self._log_canonical_event(
                event_name="playlist_ingest_summary",
                guild_id=ctx.guild.id,
                channel_id=ctx.channel.id,
                user_id=ctx.author.id,
                trigger=";playlist",
                source="command",
                accepted_count=accepted_count,
                total_items=len(playlist_data.items),
            )
            if accepted_count < len(playlist_data.items):
                await ctx.send(
                    "Queued "
                    f"{accepted_count} track(s) from the playlist before the session limit was reached. "
                    "Session closed at limit."
                )
            else:
                await ctx.send(
                    "Queued "
                    f"{accepted_count} track(s) from the playlist. Submissions are now closed."
                )

            if (
                session.autoplay_enabled
                and session.now_playing is None
                and ctx.voice_client is not None
            ):
                audio = self._get_audio(ctx).for_guild(ctx.guild.id, session)
                started = await audio.play_next(ctx.voice_client)
                if started is not None:
                    session.now_playing_channel_id = ctx.channel.id
                    embed = build_now_playing_embed(started)
                    await ctx.send(embed=embed)

        @self.command(name="q")
        async def queue(ctx: commands.Context) -> None:
            if ctx.guild is None:
                await ctx.send("This command can only be used in a server.")
                return

            session = self._get_session(ctx).for_guild(ctx.guild.id)
            lines: list[str] = []
            if session.submissions_open:
                lines.append("Session is open.")
                if isinstance(ctx.author, discord.Member) and _is_mod(ctx.author):
                    lines.append(
                        "Add a Suno URL to queue a song, or use `;playlist <url>`."
                    )
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
                    artist = track.artist_display or "Unknown Artist"
                    lines.append(
                        f"{idx}. {track.title} by {artist} (Requested by {track.requester_name})"
                    )
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

            embed = build_now_playing_embed(session.now_playing)
            await ctx.send(embed=embed)

        @self.command(name="p")
        async def play(ctx: commands.Context) -> None:
            if ctx.guild is None:
                await ctx.send("This command can only be used in a server.")
                return

            stream_session = await self._resolve_stream_session(ctx)
            if stream_session is None:
                return

            _, session = stream_session
            is_mod = isinstance(ctx.author, discord.Member) and _is_mod(ctx.author)
            full_autoplay_mode = (
                session.autoplay_enabled is True and session.autoplay_remaining is None
            )
            if not is_mod and not full_autoplay_mode:
                await ctx.send("You don't have permission to use this command.")
                return

            session.now_playing_channel_id = ctx.channel.id
            audio = self._get_audio(ctx).for_guild(ctx.guild.id, session)
            if session.now_playing is not None:
                await ctx.send(
                    f"Already playing: {session.now_playing.title}. Use ;n to skip."
                )
                return

            if not session.queue:
                if is_mod:
                    await ctx.send(
                        "Queue is empty. Drop a Suno URL or use ;playlist <Suno Playlist URL>."
                    )
                else:
                    await ctx.send("Queue is empty. Drop a Suno URL.")
                return

            started = await audio.play_next(ctx.voice_client)
            if started is None:
                if is_mod:
                    await ctx.send(
                        "Queue is empty. Drop a Suno URL or use ;playlist <Suno Playlist URL>."
                    )
                else:
                    await ctx.send("Queue is empty. Drop a Suno URL.")
                return

            session.now_playing_channel_id = ctx.channel.id
            embed = build_now_playing_embed(started)
            await ctx.send(embed=embed)
            self._log_canonical_event(
                event_name="playback_started",
                guild_id=ctx.guild.id,
                channel_id=ctx.channel.id,
                user_id=ctx.author.id,
                trigger=";p",
                source="command",
                track_title=started.title,
            )

        @self.command(name="n")
        async def skip(ctx: commands.Context) -> None:
            if ctx.guild is None or not isinstance(ctx.author, discord.Member):
                await ctx.send("This command can only be used in a server.")
                return

            if not _is_mod(ctx.author):
                await ctx.send("You don't have permission to use this command.")
                return

            if ctx.voice_client is None:
                await ctx.send("I'm not connected to a voice channel.")
                return

            stream_session = await self._resolve_stream_session(ctx)
            if stream_session is None:
                return

            _, session = stream_session
            audio = self._get_audio(ctx).for_guild(ctx.guild.id, session)
            started = await audio.skip(ctx.voice_client)
            if started is None:
                await ctx.send("Skipped. Queue is now empty; playback stopped.")
                self._log_canonical_event(
                    event_name="playback_skipped",
                    guild_id=ctx.guild.id,
                    channel_id=ctx.channel.id,
                    user_id=ctx.author.id,
                    trigger=";n",
                    source="command",
                    next_track_started=False,
                )
                return

            session.now_playing_channel_id = ctx.channel.id
            embed = build_now_playing_embed(started)
            await ctx.send(content="Skipped.", embed=embed)
            self._log_canonical_event(
                event_name="playback_skipped",
                guild_id=ctx.guild.id,
                channel_id=ctx.channel.id,
                user_id=ctx.author.id,
                trigger=";n",
                source="command",
                next_track_started=True,
                track_title=started.title,
            )

        @self.command(name="pause")
        async def pause(ctx: commands.Context) -> None:
            if ctx.guild is None or not isinstance(ctx.author, discord.Member):
                await ctx.send("This command can only be used in a server.")
                return

            if not _is_mod(ctx.author):
                await ctx.send("You don't have permission to use this command.")
                return

            if ctx.voice_client is None:
                await ctx.send("I'm not connected to a voice channel.")
                return

            stream_session = await self._resolve_stream_session(ctx)
            if stream_session is None:
                return

            if not ctx.voice_client.is_playing():
                await ctx.send("Nothing is playing right now.")
                return

            ctx.voice_client.pause()
            await ctx.send("Playback paused.")
            self._log_canonical_event(
                event_name="playback_paused",
                guild_id=ctx.guild.id,
                channel_id=ctx.channel.id,
                user_id=ctx.author.id,
                trigger=";pause",
                source="command",
            )

        @self.command(name="resume")
        async def resume(ctx: commands.Context) -> None:
            if ctx.guild is None or not isinstance(ctx.author, discord.Member):
                await ctx.send("This command can only be used in a server.")
                return

            if not _is_mod(ctx.author):
                await ctx.send("You don't have permission to use this command.")
                return

            if ctx.voice_client is None:
                await ctx.send("I'm not connected to a voice channel.")
                return

            stream_session = await self._resolve_stream_session(ctx)
            if stream_session is None:
                return

            if not ctx.voice_client.is_paused():
                await ctx.send("Playback is not paused.")
                return

            ctx.voice_client.resume()
            await ctx.send("Playback resumed.")
            self._log_canonical_event(
                event_name="playback_resumed",
                guild_id=ctx.guild.id,
                channel_id=ctx.channel.id,
                user_id=ctx.author.id,
                trigger=";resume",
                source="command",
            )

        @self.command(name="s")
        async def stop(ctx: commands.Context) -> None:
            if ctx.guild is None or not isinstance(ctx.author, discord.Member):
                await ctx.send("This command can only be used in a server.")
                return

            if not _is_mod(ctx.author):
                await ctx.send("You don't have permission to use this command.")
                return

            if ctx.voice_client is None:
                await ctx.send("I'm not connected to a voice channel.")
                return

            stream_session = await self._resolve_stream_session(ctx)
            if stream_session is None:
                return

            _, session = stream_session
            audio = self._get_audio(ctx).for_guild(ctx.guild.id, session)
            await audio.stop(ctx.voice_client)
            await ctx.send("Playback stopped.")
            self._log_canonical_event(
                event_name="playback_stopped",
                guild_id=ctx.guild.id,
                channel_id=ctx.channel.id,
                user_id=ctx.author.id,
                trigger=";s",
                source="command",
            )

        @self.command(name="clear")
        async def clear(ctx: commands.Context) -> None:
            if ctx.guild is None or not isinstance(ctx.author, discord.Member):
                await ctx.send("This command can only be used in a server.")
                return

            if not _is_mod(ctx.author):
                await ctx.send("You don't have permission to use this command.")
                return

            stream_session = await self._resolve_stream_session(ctx)
            if stream_session is None:
                return

            _, session = stream_session
            session.queue.clear()
            await ctx.send("Queue cleared.")
            self._log_canonical_event(
                event_name="queue_cleared",
                guild_id=ctx.guild.id,
                channel_id=ctx.channel.id,
                user_id=ctx.author.id,
                trigger=";clear",
                source="command",
            )

        @self.command(name="remove")
        async def remove(ctx: commands.Context, index: int) -> None:
            if ctx.guild is None or not isinstance(ctx.author, discord.Member):
                await ctx.send("This command can only be used in a server.")
                return

            if not _is_mod(ctx.author):
                await ctx.send("You don't have permission to use this command.")
                return

            stream_session = await self._resolve_stream_session(ctx)
            if stream_session is None:
                return

            _, session = stream_session
            if index < 1 or index > len(session.queue):
                await ctx.send("Invalid queue index.")
                return

            track = session.queue.pop(index - 1)
            await ctx.send(
                f"Removed: {track.title} (requested by {track.requester_name})."
            )

        @self.command(name="limit")
        async def limit(ctx: commands.Context, *args: str) -> None:
            if ctx.guild is None or not isinstance(ctx.author, discord.Member):
                await ctx.send("This command can only be used in a server.")
                return

            if not _is_mod(ctx.author):
                await ctx.send("You don't have permission to use this command.")
                return

            if not args:
                await ctx.send("Usage: `;limit <count>` or `;limit --session <count>`.")
                return

            stream_session = await self._resolve_stream_session(ctx)
            if stream_session is None:
                return

            _, session = stream_session
            if len(args) == 2 and args[0] == "--session":
                try:
                    session_limit = int(args[1])
                except ValueError:
                    await ctx.send("Session limit must be a number.")
                    return

                if session_limit < 1:
                    await ctx.send("Session limit must be at least 1.")
                    return

                session.session_total_limit = session_limit
                if self._session_limit_reached(session):
                    session.submissions_open = False
                    await self._send_scrape_failure_report(
                        guild_id=ctx.guild.id,
                        session=session,
                        moderator_channel=ctx.channel,
                        reason="session closed after setting session limit",
                    )
                await ctx.send(
                    f"Session total track cap set to {session_limit} (counts all tracks added this session)."
                )
                self._log_canonical_event(
                    event_name="limit_changed",
                    guild_id=ctx.guild.id,
                    channel_id=ctx.channel.id,
                    user_id=ctx.author.id,
                    trigger=";limit --session",
                    source="command",
                    limit_type="session_total",
                    limit_value=session_limit,
                )
                return

            if len(args) != 1:
                await ctx.send("Usage: `;limit <count>` or `;limit --session <count>`.")
                return

            try:
                limit_value = int(args[0])
            except ValueError:
                await ctx.send("Limit must be a number.")
                return

            if limit_value < 1:
                await ctx.send("Limit must be at least 1.")
                return

            session.per_user_limit = limit_value
            await ctx.send(f"Per-user submission limit set to {limit_value}.")
            self._log_canonical_event(
                event_name="limit_changed",
                guild_id=ctx.guild.id,
                channel_id=ctx.channel.id,
                user_id=ctx.author.id,
                trigger=";limit",
                source="command",
                limit_type="per_user",
                limit_value=limit_value,
            )

        @self.command(name="autoplay")
        async def autoplay(ctx: commands.Context, value: Optional[str] = None) -> None:
            if ctx.guild is None or not isinstance(ctx.author, discord.Member):
                await ctx.send("This command can only be used in a server.")
                return

            if not _is_mod(ctx.author):
                await ctx.send("You don't have permission to use this command.")
                return

            stream_session = await self._resolve_stream_session(ctx)
            if stream_session is None:
                return

            _, session = stream_session

            if value is None:
                session.now_playing_channel_id = ctx.channel.id
                session.set_autoplay(None)
                await ctx.send("Autoplay enabled until queue is empty.")
                self._log_canonical_event(
                    event_name="autoplay_changed",
                    guild_id=ctx.guild.id,
                    channel_id=ctx.channel.id,
                    user_id=ctx.author.id,
                    trigger=";autoplay",
                    source="command",
                    mode="unbounded",
                )
                return

            if value.lower() == "off":
                session.disable_autoplay()
                await ctx.send("Autoplay disabled.")
                self._log_canonical_event(
                    event_name="autoplay_changed",
                    guild_id=ctx.guild.id,
                    channel_id=ctx.channel.id,
                    user_id=ctx.author.id,
                    trigger=";autoplay off",
                    source="command",
                    mode="off",
                )
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
            self._log_canonical_event(
                event_name="autoplay_changed",
                guild_id=ctx.guild.id,
                channel_id=ctx.channel.id,
                user_id=ctx.author.id,
                trigger=";autoplay <count>",
                source="command",
                mode="bounded",
                remaining=remaining,
            )

        @self.command(name="cooldown")
        async def cooldown(ctx: commands.Context, value: Optional[str] = None) -> None:
            if ctx.guild is None or not isinstance(ctx.author, discord.Member):
                await ctx.send("This command can only be used in a server.")
                return

            if not _is_mod(ctx.author):
                await ctx.send("You don't have permission to use this command.")
                return

            stream_session = await self._resolve_stream_session(ctx)
            if stream_session is None:
                return

            _, session = stream_session

            if value is None or value.lower() == "on":
                session.cooldown_mode = CooldownMode.TIME
                session.submission_cooldown_seconds = 15 * 60
                await ctx.send("Submission cooldown set to 15 minutes.")
                self._log_canonical_event(
                    event_name="cooldown_mode_changed",
                    guild_id=ctx.guild.id,
                    channel_id=ctx.channel.id,
                    user_id=ctx.author.id,
                    trigger=";cooldown",
                    source="command",
                    mode="time",
                    cooldown_seconds=session.submission_cooldown_seconds,
                )
                return

            lowered = value.lower()

            if lowered == "-queue":
                session.cooldown_mode = CooldownMode.QUEUE
                await ctx.send(
                    "Submission cooldown set to queue mode (one active track per user)."
                )
                self._log_canonical_event(
                    event_name="cooldown_mode_changed",
                    guild_id=ctx.guild.id,
                    channel_id=ctx.channel.id,
                    user_id=ctx.author.id,
                    trigger=";cooldown -queue",
                    source="command",
                    mode="queue",
                )
                return

            if lowered == "off":
                session.cooldown_mode = CooldownMode.OFF
                session.submission_cooldown_seconds = 0
                await ctx.send("⚠️ Submission cooldown has been deactivated.")
                self._log_canonical_event(
                    event_name="cooldown_mode_changed",
                    guild_id=ctx.guild.id,
                    channel_id=ctx.channel.id,
                    user_id=ctx.author.id,
                    trigger=";cooldown off",
                    source="command",
                    mode="off",
                )
                return

            try:
                minutes = int(value)
            except ValueError:
                await ctx.send(
                    "Cooldown value must be a number of minutes, '-queue', or 'off'."
                )
                return

            if minutes < 1:
                await ctx.send("Cooldown minutes must be at least 1.")
                return

            session.cooldown_mode = CooldownMode.TIME
            session.submission_cooldown_seconds = minutes * 60
            await ctx.send(f"Submission cooldown set to {minutes} minute(s).")
            self._log_canonical_event(
                event_name="cooldown_mode_changed",
                guild_id=ctx.guild.id,
                channel_id=ctx.channel.id,
                user_id=ctx.author.id,
                trigger=";cooldown <minutes>",
                source="command",
                mode="time",
                cooldown_seconds=session.submission_cooldown_seconds,
            )

        @self.command(name="dj")
        async def dj(ctx: commands.Context, value: Optional[str] = None) -> None:
            if ctx.guild is None or not isinstance(ctx.author, discord.Member):
                await ctx.send("This command can only be used in a server.")
                return

            if not _is_mod(ctx.author):
                await ctx.send("You don't have permission to use this command.")
                return

            stream_session = await self._resolve_stream_session(ctx)
            if stream_session is None:
                return

            _, session = stream_session

            if value is None:
                session.now_playing_channel_id = ctx.channel.id
                session.set_dj(None)
                await ctx.send("DJ mode enabled until queue is empty.")
                self._log_canonical_event(
                    event_name="dj_mode_changed",
                    guild_id=ctx.guild.id,
                    channel_id=ctx.channel.id,
                    user_id=ctx.author.id,
                    trigger=";dj",
                    source="command",
                    mode="unbounded",
                )
                return

            if value.lower() == "off":
                session.disable_dj()
                await ctx.send("DJ mode disabled.")
                self._log_canonical_event(
                    event_name="dj_mode_changed",
                    guild_id=ctx.guild.id,
                    channel_id=ctx.channel.id,
                    user_id=ctx.author.id,
                    trigger=";dj off",
                    source="command",
                    mode="off",
                )
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
            self._log_canonical_event(
                event_name="dj_mode_changed",
                guild_id=ctx.guild.id,
                channel_id=ctx.channel.id,
                user_id=ctx.author.id,
                trigger=";dj <count>",
                source="command",
                mode="bounded",
                remaining=remaining,
            )


def build_bot() -> JukeBot:
    """
    Construct the bot with all dependencies wired.
    Keeps global scope clean and avoids import-time side effects.
    """
    settings = load_bot_settings()

    intents = discord.Intents.default()
    intents.message_content = True  # required for prefix commands

    deps = BotDeps(
        session_manager=SessionManager(),
        audio_manager=AudioControllerManager(),
        ingest_use_case=IngestSunoLink(
            suno_client=FallbackSunoClient(),
            track_repo=PostgresTrackRepository(async_session_factory),
            submission_repo=PostgresSubmissionRepository(async_session_factory),
            queue_repo=PostgresQueueRepository(async_session_factory),
        ),
        playlist_client=HttpxSunoPlaylistClient(),
        submission_repo=PostgresSubmissionRepository(async_session_factory),
        queue_repo=PostgresQueueRepository(async_session_factory),
    )

    return JukeBot(
        settings=settings,
        deps=deps,
        command_prefix=";",
        intents=intents,
    )


def main() -> None:
    """Process entrypoint."""
    configure_logging()
    bot = build_bot()
    bot.run(bot.settings.active_discord_token)


if __name__ == "__main__":
    main()
