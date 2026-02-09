from pathlib import Path
import sys
from types import SimpleNamespace
import discord
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.extend(
    [
        str(ROOT / "apps" / "bot"),
        str(ROOT / "packages" / "core"),
        str(ROOT / "packages" / "infra"),
    ]
)

from jukebotx_bot.discord.audio import AudioControllerManager
from jukebotx_bot.discord.session import CooldownMode, SessionManager, Track
from jukebotx_bot.main import BotDeps, JukeBot


class FakeMember:
    def __init__(self, user_id: int, *, is_mod: bool = True, bot: bool = False) -> None:
        self.id = user_id
        self.bot = bot
        self.display_name = f"user-{user_id}"
        self.mention = f"<@{user_id}>"
        perms = SimpleNamespace(administrator=is_mod, manage_guild=is_mod)
        self.guild_permissions = perms
        self.roles = []


class FakeGuild:
    def __init__(self, guild_id: int) -> None:
        self.id = guild_id
        self.voice_client = object()


class FakeChannel:
    def __init__(self, channel_id: int) -> None:
        self.id = channel_id
        self.messages: list[str] = []

    async def send(self, content: str) -> None:
        self.messages.append(content)


class FakeContext:
    def __init__(self, guild: FakeGuild, author: FakeMember, channel: FakeChannel) -> None:
        self.guild = guild
        self.author = author
        self.channel = channel
        self.sent: list[str] = []
        self.command = None
        self.invoked_with = None

    async def send(self, content: str) -> None:
        self.sent.append(content)


class FakeMessage:
    def __init__(self, guild: FakeGuild, author: FakeMember, channel: FakeChannel, content: str) -> None:
        self.guild = guild
        self.author = author
        self.channel = channel
        self.content = content
        self.id = 222
        self.reactions: list[str] = []

    async def add_reaction(self, emoji: str) -> None:
        self.reactions.append(emoji)


class FakeIngestUseCase:
    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, _input):
        self.calls += 1
        return SimpleNamespace(
            mp3_url="https://cdn.suno.ai/test.mp3",
            track_id=None,
            suno_url="https://suno.com/song/abc",
            track_title="Test",
            artist_display="Artist",
            media_url=None,
        )


@pytest.fixture
def bot(monkeypatch: pytest.MonkeyPatch) -> JukeBot:
    monkeypatch.setattr("jukebotx_bot.main.discord.Member", FakeMember)
    deps = BotDeps(
        session_manager=SessionManager(),
        ingest_use_case=FakeIngestUseCase(),
        audio_manager=AudioControllerManager(),
        playlist_client=SimpleNamespace(),
        submission_repo=SimpleNamespace(),
        queue_repo=SimpleNamespace(),
    )
    settings = SimpleNamespace(env="development", opus_api_base_url=None)
    intents = discord.Intents.none()
    return JukeBot(settings=settings, deps=deps, command_prefix=";", intents=intents)


@pytest.mark.asyncio
async def test_cooldown_command_paths(bot: JukeBot) -> None:
    guild = FakeGuild(1)
    channel = FakeChannel(10)
    author = FakeMember(100, is_mod=True)
    ctx = FakeContext(guild, author, channel)
    command = bot.get_command("cooldown")
    assert command is not None

    session = bot.deps.session_manager.for_guild(guild.id)

    await command.callback(ctx, None)
    assert session.cooldown_mode == CooldownMode.TIME
    assert session.submission_cooldown_seconds == 15 * 60

    await command.callback(ctx, "30")
    assert session.cooldown_mode == CooldownMode.TIME
    assert session.submission_cooldown_seconds == 30 * 60

    await command.callback(ctx, "-queue")
    assert session.cooldown_mode == CooldownMode.QUEUE

    await command.callback(ctx, "off")
    assert session.cooldown_mode == CooldownMode.OFF
    assert session.submission_cooldown_seconds == 0


@pytest.mark.asyncio
async def test_on_message_blocks_when_queue_mode_user_already_has_track(bot: JukeBot) -> None:
    guild = FakeGuild(2)
    channel = FakeChannel(11)
    author = FakeMember(200, is_mod=False)
    message = FakeMessage(guild, author, channel, "https://suno.com/song/abc")
    ctx = FakeContext(guild, author, channel)

    async def fake_get_context(_message):
        return ctx

    async def fake_process_commands(_message):
        return None

    bot.get_context = fake_get_context
    bot.process_commands = fake_process_commands

    session = bot.deps.session_manager.for_guild(guild.id)
    session.cooldown_mode = CooldownMode.QUEUE
    session.queue.append(
        Track(
            audio_url="a",
            opus_url=None,
            page_url=None,
            title="Queued",
            artist_display=None,
            media_url=None,
            requester_id=author.id,
            requester_name="user",
        )
    )

    await bot.on_message(message)

    assert bot.deps.ingest_use_case.calls == 0


@pytest.mark.asyncio
async def test_on_message_blocks_when_queue_mode_user_is_now_playing(bot: JukeBot) -> None:
    guild = FakeGuild(3)
    channel = FakeChannel(12)
    author = FakeMember(300, is_mod=False)
    message = FakeMessage(guild, author, channel, "https://suno.com/song/abc")
    ctx = FakeContext(guild, author, channel)

    async def fake_get_context(_message):
        return ctx

    async def fake_process_commands(_message):
        return None

    bot.get_context = fake_get_context
    bot.process_commands = fake_process_commands

    session = bot.deps.session_manager.for_guild(guild.id)
    session.cooldown_mode = CooldownMode.QUEUE
    session.now_playing = Track(
        audio_url="a",
        opus_url=None,
        page_url=None,
        title="Now Playing",
        artist_display=None,
        media_url=None,
        requester_id=author.id,
        requester_name="user",
    )

    await bot.on_message(message)

    assert bot.deps.ingest_use_case.calls == 0
