# apps/bot/jukebotx_bot/main.py
import discord
from discord.ext import commands

from jukebotx_bot.discord.session import SessionManager, Track
from jukebotx_bot.discord.suno import extract_suno_urls
from jukebotx_bot.settings import load_bot_settings
from jukebotx_core.use_cases.ingest_suno_links import IngestSunoLink, IngestSunoLinkInput
from jukebotx_infra.db import async_session_factory, init_db
from jukebotx_infra.repos.queue_repo import PostgresQueueRepository
from jukebotx_infra.repos.submission_repo import PostgresSubmissionRepository
from jukebotx_infra.repos.track_repo import PostgresTrackRepository
from jukebotx_infra.suno.client import HttpxSunoClient, SunoScrapeError


def _is_mod(member: discord.Member) -> bool:
    perms = member.guild_permissions
    return bool(perms.administrator or perms.manage_guild)


def main() -> None:
    settings = load_bot_settings()

    intents = discord.Intents.default()
    intents.message_content = True

    bot = commands.Bot(command_prefix=";", intents=intents)
    session_manager = SessionManager()
    ingest_use_case = IngestSunoLink(
        suno_client=HttpxSunoClient(),
        track_repo=PostgresTrackRepository(async_session_factory),
        submission_repo=PostgresSubmissionRepository(async_session_factory),
        queue_repo=PostgresQueueRepository(async_session_factory),
    )

    @bot.setup_hook
    async def setup_hook() -> None:
        await init_db()

    @bot.event
    async def on_ready() -> None:
        """
        Discord.py lifecycle hook fired when the client has successfully connected
        and the bot user identity is available.

        This is the earliest safe place to validate that the *actual Discord identity*
        matches the expected environment (dev vs prod). These checks prevent
        accidentally running a production deployment with the dev bot token, or vice versa.
        """
        # Defensive: discord.py guarantees `client.user` in on_ready, but keep this explicit
        # so failures are obvious if lifecycle behavior changes.
        assert bot.user is not None, "client.user is unexpectedly None in on_ready()"

        bot_name = bot.user.name.lower().strip()
        env = settings.env.lower().strip()

        # 1) Never allow a dev-named bot identity to run in production.
        assert (
            env != "production" or "dev" not in bot_name
        ), (
            "Safety check failed: ENV=production but the connected Discord bot name "
            "contains 'dev'. You are likely using the DEV bot token in production."
        )

        # 2) In development, require a dev-named bot identity to reduce the chance
        # of accidentally using the production bot token while testing.
        assert (
            env != "development" or "dev" in bot_name
        ), (
            "Safety check failed: ENV=development but the connected Discord bot name "
            "does NOT contain 'dev'. You are likely using the production bot token in development."
        )

        print(f"Connected as {bot.user} (env={settings.env})")

    @bot.event
    async def on_message(message: discord.Message) -> None:
        if message.author.bot:
            return

        if message.guild is None:
            await bot.process_commands(message)
            return

        if message.guild.voice_client is None:
            await bot.process_commands(message)
            return

        urls = extract_suno_urls(message.content)
        if not urls:
            await bot.process_commands(message)
            return

        added_any = False
        for url in urls:
            try:
                result = await ingest_use_case.execute(
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

        if added_any:
            try:
                await message.add_reaction("🤘")
            except discord.HTTPException:
                pass

        await bot.process_commands(message)

    def get_session(ctx: commands.Context) -> SessionManager:
        return session_manager

    @bot.command(name="join")
    async def join(ctx: commands.Context) -> None:
        if ctx.guild is None or not isinstance(ctx.author, discord.Member):
            await ctx.send("This command can only be used in a server.")
            return

        voice_state = ctx.author.voice
        if voice_state is None or voice_state.channel is None:
            await ctx.send("Join a voice channel first.")
            return

        if ctx.voice_client is None:
            await voice_state.channel.connect()
        else:
            await ctx.voice_client.move_to(voice_state.channel)

        await ctx.send(f"Joined {voice_state.channel.name}.")

    @bot.command(name="leave")
    async def leave(ctx: commands.Context) -> None:
        if ctx.guild is None:
            await ctx.send("This command can only be used in a server.")
            return

        session = get_session(ctx).for_guild(ctx.guild.id)
        session.reset()

        if ctx.voice_client is not None:
            await ctx.voice_client.disconnect()

        await ctx.send("Left the voice channel. Session reset.")

    @bot.command(name="ping")
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

        if settings.jam_session_channel_id is None:
            await ctx.send("Jam session channel is not configured.")
            return

        channel = ctx.guild.get_channel(settings.jam_session_channel_id)
        if channel is None:
            await ctx.send("Jam session channel not found.")
            return

        if target_norm == "here":
            mention = "@here"
        else:
            if settings.jam_session_role_id is None:
                await ctx.send("Jam session role is not configured.")
                return
            mention = f"<@&{settings.jam_session_role_id}>"

        await channel.send(f"{mention} Submissions are open! {message}")
        await ctx.send("Announcement sent.")

    @bot.command(name="open")
    async def open_submissions(ctx: commands.Context) -> None:
        if ctx.guild is None:
            await ctx.send("This command can only be used in a server.")
            return

        session = get_session(ctx).for_guild(ctx.guild.id)
        session.submissions_open = True
        session.reset_submission_counts()
        await ctx.send("Submissions are open.")

    @bot.command(name="close")
    async def close_submissions(ctx: commands.Context) -> None:
        if ctx.guild is None:
            await ctx.send("This command can only be used in a server.")
            return

        session = get_session(ctx).for_guild(ctx.guild.id)
        session.submissions_open = False
        await ctx.send("Submissions are closed.")

    @bot.command(name="add")
    async def add(ctx: commands.Context, url: str) -> None:
        if ctx.guild is None or not isinstance(ctx.author, discord.Member):
            await ctx.send("This command can only be used in a server.")
            return

        if ctx.voice_client is None:
            await ctx.send("Use ;join first.")
            return

        session = get_session(ctx).for_guild(ctx.guild.id)

        if not session.submissions_open and not _is_mod(ctx.author):
            await ctx.send("Submissions are closed.")
            return

        user_id = ctx.author.id
        if session.per_user_limit is not None and not _is_mod(ctx.author):
            current = session.per_user_counts.get(user_id, 0)
            if current >= session.per_user_limit:
                await ctx.send("You have reached the submission limit for this session.")
                return

        track = Track(
            url=url,
            title=url,
            requester_id=ctx.author.id,
            requester_name=ctx.author.display_name,
        )
        session.queue.append(track)
        session.per_user_counts[user_id] = session.per_user_counts.get(user_id, 0) + 1

        await ctx.send(f"Queued: {track.title} (requested by {track.requester_name}).")

        if session.autoplay_enabled and session.now_playing is None:
            started = session.start_next_track()
            if started is not None:
                await ctx.send(f"Now playing: {started.title} (requested by {started.requester_name}).")

    @bot.command(name="q")
    async def queue(ctx: commands.Context) -> None:
        if ctx.guild is None:
            await ctx.send("This command can only be used in a server.")
            return

        session = get_session(ctx).for_guild(ctx.guild.id)
        lines: list[str] = []

        if session.now_playing is not None:
            lines.append(
                f"Now Playing: {session.now_playing.title} (requested by {session.now_playing.requester_name})"
            )
        else:
            lines.append("Now Playing: nothing")

        if session.queue:
            lines.append("Up next:")
            for idx, track in enumerate(session.queue[:5], start=1):
                lines.append(f"{idx}. {track.title} (requested by {track.requester_name})")
        else:
            lines.append("Queue is empty.")

        await ctx.send("\n".join(lines))

    @bot.command(name="np")
    async def now_playing(ctx: commands.Context) -> None:
        if ctx.guild is None:
            await ctx.send("This command can only be used in a server.")
            return

        session = get_session(ctx).for_guild(ctx.guild.id)
        if session.now_playing is None:
            await ctx.send("Nothing is playing.")
            return

        await ctx.send(
            f"Now Playing: {session.now_playing.title} (requested by {session.now_playing.requester_name})"
        )

    @bot.command(name="p")
    async def play(ctx: commands.Context) -> None:
        if ctx.guild is None:
            await ctx.send("This command can only be used in a server.")
            return

        session = get_session(ctx).for_guild(ctx.guild.id)
        if session.now_playing is not None:
            await ctx.send(f"Already playing: {session.now_playing.title}. Use ;n to skip.")
            return

        if not session.queue:
            await ctx.send("Queue is empty. Use ;add <url>.")
            return

        started = session.start_next_track()
        if started is None:
            await ctx.send("Queue is empty. Use ;add <url>.")
            return

        await ctx.send(f"Now playing: {started.title} (requested by {started.requester_name}).")

    @bot.command(name="n")
    async def skip(ctx: commands.Context) -> None:
        if ctx.guild is None or not isinstance(ctx.author, discord.Member):
            await ctx.send("This command can only be used in a server.")
            return

        if not _is_mod(ctx.author):
            await ctx.send("You don't have permission to use this command.")
            return

        session = get_session(ctx).for_guild(ctx.guild.id)
        session.stop_playback()
        started = session.start_next_track()
        if started is None:
            await ctx.send("Skipped. Queue is now empty; playback stopped.")
            return

        await ctx.send(f"Skipped. Now playing: {started.title} (requested by {started.requester_name}).")

    @bot.command(name="s")
    async def stop(ctx: commands.Context) -> None:
        if ctx.guild is None or not isinstance(ctx.author, discord.Member):
            await ctx.send("This command can only be used in a server.")
            return

        if not _is_mod(ctx.author):
            await ctx.send("You don't have permission to use this command.")
            return

        session = get_session(ctx).for_guild(ctx.guild.id)
        session.stop_playback()
        await ctx.send("Playback stopped.")

    @bot.command(name="clear")
    async def clear(ctx: commands.Context) -> None:
        if ctx.guild is None or not isinstance(ctx.author, discord.Member):
            await ctx.send("This command can only be used in a server.")
            return

        if not _is_mod(ctx.author):
            await ctx.send("You don't have permission to use this command.")
            return

        session = get_session(ctx).for_guild(ctx.guild.id)
        session.queue.clear()
        await ctx.send("Queue cleared.")

    @bot.command(name="remove")
    async def remove(ctx: commands.Context, index: int) -> None:
        if ctx.guild is None or not isinstance(ctx.author, discord.Member):
            await ctx.send("This command can only be used in a server.")
            return

        if not _is_mod(ctx.author):
            await ctx.send("You don't have permission to use this command.")
            return

        session = get_session(ctx).for_guild(ctx.guild.id)
        if index < 1 or index > len(session.queue):
            await ctx.send("Invalid queue index.")
            return

        track = session.queue.pop(index - 1)
        await ctx.send(f"Removed: {track.title} (requested by {track.requester_name}).")

    @bot.command(name="limit")
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

        session = get_session(ctx).for_guild(ctx.guild.id)
        session.per_user_limit = limit_value
        await ctx.send(f"Per-user submission limit set to {limit_value}.")

    @bot.command(name="autoplay")
    async def autoplay(ctx: commands.Context, value: str | None = None) -> None:
        if ctx.guild is None or not isinstance(ctx.author, discord.Member):
            await ctx.send("This command can only be used in a server.")
            return

        if not _is_mod(ctx.author):
            await ctx.send("You don't have permission to use this command.")
            return

        session = get_session(ctx).for_guild(ctx.guild.id)

        if value is None:
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

        session.set_autoplay(remaining)
        await ctx.send(f"Autoplay enabled for the next {remaining} track(s).")

    @bot.command(name="dj")
    async def dj(ctx: commands.Context, value: str | None = None) -> None:
        if ctx.guild is None or not isinstance(ctx.author, discord.Member):
            await ctx.send("This command can only be used in a server.")
            return

        if not _is_mod(ctx.author):
            await ctx.send("You don't have permission to use this command.")
            return

        session = get_session(ctx).for_guild(ctx.guild.id)

        if value is None:
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

        session.set_dj(remaining)
        await ctx.send(f"DJ mode enabled for the next {remaining} track(s).")

    bot.run(settings.active_discord_token)


if __name__ == "__main__":
    main()
