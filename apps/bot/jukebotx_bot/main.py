# apps/bot/jukebotx_bot/main.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
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
from jukebotx_bot.discord.session import SessionManager, Track
from jukebotx_bot.discord.suno import extract_suno_urls
from jukebotx_bot.settings import load_bot_settings
from jukebotx_core.use_cases.ingest_suno_links import IngestSunoLink, IngestSunoLinkInput
from jukebotx_infra.db import async_session_factory, init_db
from jukebotx_infra.repos.queue_repo import PostgresQueueRepository
from jukebotx_infra.repos.submission_repo import PostgresSubmissionRepository
from jukebotx_infra.repos.track_repo import PostgresTrackRepository
from jukebotx_infra.suno.client import HttpxSunoClient, SunoScrapeError
from jukebotx_infra.suno.playlist_client import HttpxSunoPlaylistClient


def _is_mod(member: discord.Member) -> bool:
    """Return True if the member has server-level moderation permissions."""
    perms = member.guild_permissions
    return bool(perms.administrator or perms.manage_guild)


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

        logging.basicConfig(level=logging.INFO)

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
            logging.warning("Failed to prefetch opus status for %s: %s", track_id, exc)

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
            is_host = isinstance(message.author, discord.Member) and _is_mod(message.author)
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

    # -----------------------------
    # Commands
    # -----------------------------
    def _register_commands(self) -> None:
        @self.command(name="help")
        async def help_command(ctx: commands.Context) -> None:
            embed = discord.Embed(
                title="JukeBotx Help",
                description=(
                    "Command prefix: `;`\n"
                    "Drop Suno links in chat to queue when submissions are open. "
                    "Use `;playlist <url>` for Suno playlists (mods only)."
                ),
                color=discord.Color.blurple(),
            )
            embed.add_field(
                name="Session",
                value=(
                    "`;join` — Join your voice channel.\n"
                    "`;leave` — Leave and reset the session.\n"
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
            embed.add_field(
                name="Queue Management (mods)",
                value=(
                    "`;playlist <url>` — Queue a Suno playlist and close submissions.\n"
                    "`;clear` — Clear the queue.\n"
                    "`;remove <index>` — Remove a queued item.\n"
                    "`;limit <count>` — Set per-user submission limit."
                ),
                inline=False,
            )
            embed.add_field(
                name="Autoplay + DJ Mode (mods)",
                value=(
                    "`;autoplay` — Enable autoplay until the queue ends.\n"
                    "`;autoplay <count>` — Play the next N tracks.\n"
                    "`;autoplay off` — Disable autoplay.\n"
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
            if ctx.guild is None or not isinstance(ctx.author, discord.Member):
                await ctx.send("This command can only be used in a server.")
                return

            if ctx.author.voice is None or ctx.author.voice.channel is None:
                await ctx.send("You're not in a voice channel!")
                return

            channel = ctx.author.voice.channel

            try:
                await channel.connect()
            except discord.Forbidden:
                await ctx.send("🚫 I don't have permission to join that voice channel (View/Connect).")
                return
            except Exception as exc:
                await ctx.send(f"⚠️ Failed to join VC: {type(exc).__name__}: {exc}")
                raise

            await ctx.send(f"Joined {channel.name}!")


        @self.command(name="leave")
        async def leave(ctx: commands.Context) -> None:
            if ctx.guild is None:
                await ctx.send("This command can only be used in a server.")
                return

            session = self._get_session(ctx).for_guild(ctx.guild.id)
            session.reset()

            if ctx.voice_client is not None:
                audio = self._get_audio(ctx).for_guild(ctx.guild.id, session)
                await audio.stop(ctx.voice_client)
                await ctx.voice_client.disconnect()

            await self.deps.queue_repo.clear(guild_id=ctx.guild.id)
            await self.deps.submission_repo.clear_for_channel(
                guild_id=ctx.guild.id,
                channel_id=ctx.channel.id,
            )

            await ctx.send("Left the voice channel. Session reset.")

        @self.command(name="setlist")
        async def setlist(ctx: commands.Context) -> None:
            if ctx.guild is None or not isinstance(ctx.author, discord.Member):
                await ctx.send("This command can only be used in a server.")
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

            session = self._get_session(ctx).for_guild(ctx.guild.id)
            session.submissions_open = True
            session.reset_submission_counts()
            await ctx.send("Submissions are open.")

        @self.command(name="close")
        async def close_submissions(ctx: commands.Context) -> None:
            if ctx.guild is None:
                await ctx.send("This command can only be used in a server.")
                return

            session = self._get_session(ctx).for_guild(ctx.guild.id)
            session.submissions_open = False
            await ctx.send("Submissions are closed.")

        @self.command(name="web", aliases=["sessionurl"])
        async def web(ctx: commands.Context) -> None:
            if ctx.guild is None:
                await ctx.send("This command can only be used in a server.")
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

            if not _is_mod(ctx.author):
                await ctx.send("You don't have permission to use this command.")
                return

            if ctx.voice_client is None:
                await ctx.send("Use ;join first.")
                return

            session = self._get_session(ctx).for_guild(ctx.guild.id)
            session.now_playing_channel_id = ctx.channel.id

            if not session.submissions_open and not _is_mod(ctx.author):
                await ctx.send("Submissions are closed.")
                return

            if "https://suno.com/playlist/" not in url:
                await ctx.send("Please provide a Suno playlist URL like https://suno.com/playlist/....")
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

            session.submissions_open = False
            await ctx.send(
                "Queued "
                f"{len(playlist_data.items)} track(s) from the playlist. Submissions are now closed."
            )

            if session.autoplay_enabled and session.now_playing is None and ctx.voice_client is not None:
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

            embed = build_now_playing_embed(session.now_playing)
            await ctx.send(embed=embed)

        @self.command(name="p")
        async def play(ctx: commands.Context) -> None:
            if ctx.guild is None:
                await ctx.send("This command can only be used in a server.")
                return

            session = self._get_session(ctx).for_guild(ctx.guild.id)
            session.now_playing_channel_id = ctx.channel.id
            audio = self._get_audio(ctx).for_guild(ctx.guild.id, session)
            if session.now_playing is not None:
                await ctx.send(f"Already playing: {session.now_playing.title}. Use ;n to skip.")
                return

            if not session.queue:
                if isinstance(ctx.author, discord.Member) and _is_mod(ctx.author):
                    await ctx.send(
                        "Queue is empty. Drop a Suno URL or use ;playlist <Suno Playlist URL>."
                    )
                else:
                    await ctx.send("Queue is empty. Drop a Suno URL.")
                return

            started = await audio.play_next(ctx.voice_client)
            if started is None:
                if isinstance(ctx.author, discord.Member) and _is_mod(ctx.author):
                    await ctx.send(
                        "Queue is empty. Drop a Suno URL or use ;playlist <Suno Playlist URL>."
                    )
                else:
                    await ctx.send("Queue is empty. Drop a Suno URL.")
                return

            session.now_playing_channel_id = ctx.channel.id
            embed = build_now_playing_embed(started)
            await ctx.send(embed=embed)

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

            session = self._get_session(ctx).for_guild(ctx.guild.id)
            audio = self._get_audio(ctx).for_guild(ctx.guild.id, session)
            started = await audio.skip(ctx.voice_client)
            if started is None:
                await ctx.send("Skipped. Queue is now empty; playback stopped.")
                return

            session.now_playing_channel_id = ctx.channel.id
            embed = build_now_playing_embed(started)
            await ctx.send(content="Skipped.", embed=embed)

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

            session = self._get_session(ctx).for_guild(ctx.guild.id)
            audio = self._get_audio(ctx).for_guild(ctx.guild.id, session)
            await audio.stop(ctx.voice_client)
            await ctx.send("Playback stopped.")

        @self.command(name="clear")
        async def clear(ctx: commands.Context) -> None:
            if ctx.guild is None or not isinstance(ctx.author, discord.Member):
                await ctx.send("This command can only be used in a server.")
                return

            if not _is_mod(ctx.author):
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

            if not _is_mod(ctx.author):
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

            if not _is_mod(ctx.author):
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

            if not _is_mod(ctx.author):
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

        @self.command(name="dj")
        async def dj(ctx: commands.Context, value: Optional[str] = None) -> None:
            if ctx.guild is None or not isinstance(ctx.author, discord.Member):
                await ctx.send("This command can only be used in a server.")
                return

            if not _is_mod(ctx.author):
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

    intents = discord.Intents.default()
    intents.message_content = True  # required for prefix commands

    deps = BotDeps(
        session_manager=SessionManager(),
        audio_manager=AudioControllerManager(),
        ingest_use_case=IngestSunoLink(
            suno_client=HttpxSunoClient(),
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
    bot = build_bot()
    bot.run(bot.settings.active_discord_token)


if __name__ == "__main__":
    main()
