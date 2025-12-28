# apps/bot/jukebotx_bot/main.py
from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Optional

import discord
from discord.ext import commands

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

    # -----------------------------
    # Events
    # -----------------------------
    def _register_events(self) -> None:
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
            for url in urls:
                if "https://suno.com/playlist/" in url:
                    skipped_playlist = True
                    continue
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

                if not result.is_duplicate_in_guild:
                    added_any = True
                    
                # inside on_message, after successful ingest and not duplicate
                session = self.deps.session_manager.for_guild(message.guild.id)

                # If you want to respect close/limit logic:
                if not session.submissions_open:
                    continue

                if not result.mp3_url:
                    logging.warning("Skipping Suno URL without mp3_url: %s", url)
                    continue

                track = Track(
                    audio_url=result.mp3_url,
                    page_url=url,
                    title=result.track_title or url,
                    requester_id=message.author.id,
                    requester_name=getattr(message.author, "display_name", "unknown"),
                )
                session.queue.append(track)
                session.per_user_counts[track.requester_id] = session.per_user_counts.get(track.requester_id, 0) + 1


            if added_any:
                try:
                    await message.add_reaction("🤘")
                except discord.HTTPException:
                    pass

            if skipped_playlist:
                await message.channel.send("Playlist links aren’t auto-ingested. Use `;playlist <url>` instead.")

            await self.process_commands(message)

    # -----------------------------
    # Commands
    # -----------------------------
    def _register_commands(self) -> None:
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

            await ctx.send("Left the voice channel. Session reset.")

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

                if item.suno_track_url is not None:
                    try:
                        ingest_result = await self.deps.ingest_use_case.execute(
                            IngestSunoLinkInput(
                                guild_id=ctx.guild.id,
                                channel_id=ctx.channel.id,
                                message_id=ctx.message.id,
                                author_id=ctx.author.id,
                                suno_url=item.suno_track_url,
                            )
                        )
                    except SunoScrapeError as exc:
                        logging.warning("Failed to ingest Suno URL %s: %s", item.suno_track_url, exc)
                    else:
                        if ingest_result.track_title:
                            track_title = ingest_result.track_title
                        if ingest_result.mp3_url:
                            audio_url = ingest_result.mp3_url

                track = Track(
                    audio_url=audio_url,
                    page_url=page_url,
                    title=track_title,
                    requester_id=ctx.author.id,
                    requester_name=ctx.author.display_name,
                )
                session.queue.append(track)
                session.per_user_counts[user_id] = session.per_user_counts.get(user_id, 0) + 1

            session.submissions_open = False
            await ctx.send(
                "Queued "
                f"{len(playlist_data.items)} track(s) from the playlist. Submissions are now closed."
            )

            if session.autoplay_enabled and session.now_playing is None and ctx.voice_client is not None:
                audio = self._get_audio(ctx).for_guild(ctx.guild.id, session)
                started = await audio.play_next(ctx.voice_client)
                if started is not None:
                    embed = build_now_playing_embed(started)
                    await ctx.send(embed=embed)

        @self.command(name="q")
        async def queue(ctx: commands.Context) -> None:
            if ctx.guild is None:
                await ctx.send("This command can only be used in a server.")
                return

            session = self._get_session(ctx).for_guild(ctx.guild.id)
            lines: list[str] = []

            if session.queue:
                lines.append("Up next:")
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
                await ctx.send("Queue is empty. Use ;playlist <url>.")
                return

            started = await audio.play_next(ctx.voice_client)
            if started is None:
                await ctx.send("Queue is empty. Use ;playlist <url>.")
                return

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
