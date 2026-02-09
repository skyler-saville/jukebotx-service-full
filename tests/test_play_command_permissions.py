import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.extend(
    [
        str(ROOT / "apps" / "bot"),
        str(ROOT / "packages" / "core"),
        str(ROOT / "packages" / "infra"),
    ]
)

import discord

from jukebotx_bot.discord.session import SessionManager
from jukebotx_bot.main import BotDeps, JukeBot


class FakeMember:
    def __init__(self, *, is_mod: bool) -> None:
        self.is_mod = is_mod


class FakeAudioController:
    def __init__(self, started=None) -> None:
        self.started = started
        self.play_next_calls = 0

    async def play_next(self, voice_client):
        self.play_next_calls += 1
        return self.started


class FakeAudioManager:
    def __init__(self, controller: FakeAudioController) -> None:
        self.controller = controller

    def for_guild(self, guild_id, session):
        return self.controller


class FakeCtx:
    def __init__(self, *, author, guild_id: int = 1, channel_id: int = 2) -> None:
        self.guild = SimpleNamespace(id=guild_id)
        self.channel = SimpleNamespace(id=channel_id)
        self.author = author
        self.voice_client = None
        self.sent_messages: list[str] = []

    async def send(self, content=None, embed=None):
        self.sent_messages.append(content if content is not None else "<embed>")


def _build_bot(controller: FakeAudioController) -> JukeBot:
    settings = SimpleNamespace(env="development", opus_api_base_url=None)
    deps = BotDeps(
        session_manager=SessionManager(),
        ingest_use_case=None,
        audio_manager=FakeAudioManager(controller),
        playlist_client=None,
        submission_repo=None,
        queue_repo=None,
    )
    return JukeBot(
        settings=settings,
        deps=deps,
        command_prefix=";",
        intents=discord.Intents.none(),
    )


def test_play_denies_non_mod_when_not_full_autoplay(monkeypatch) -> None:
    from jukebotx_bot import main as main_module

    monkeypatch.setattr(main_module.discord, "Member", FakeMember)
    monkeypatch.setattr(main_module, "_is_mod", lambda member: member.is_mod)

    controller = FakeAudioController()
    bot = _build_bot(controller)
    command = bot.get_command("p")
    assert command is not None

    ctx = FakeCtx(author=FakeMember(is_mod=False))

    asyncio.run(command.callback(ctx))

    assert ctx.sent_messages == ["You don't have permission to use this command."]
    assert controller.play_next_calls == 0


def test_play_allows_non_mod_during_full_autoplay_and_keeps_queue_empty_message(monkeypatch) -> None:
    from jukebotx_bot import main as main_module

    monkeypatch.setattr(main_module.discord, "Member", FakeMember)
    monkeypatch.setattr(main_module, "_is_mod", lambda member: member.is_mod)

    controller = FakeAudioController(started=None)
    bot = _build_bot(controller)
    session = bot.deps.session_manager.for_guild(1)
    session.autoplay_enabled = True
    session.autoplay_remaining = None

    command = bot.get_command("p")
    assert command is not None

    ctx = FakeCtx(author=FakeMember(is_mod=False))

    asyncio.run(command.callback(ctx))

    assert ctx.sent_messages == ["Queue is empty. Drop a Suno URL."]
    assert controller.play_next_calls == 1


def test_play_mod_queue_empty_message_includes_playlist_hint(monkeypatch) -> None:
    from jukebotx_bot import main as main_module

    monkeypatch.setattr(main_module.discord, "Member", FakeMember)
    monkeypatch.setattr(main_module, "_is_mod", lambda member: member.is_mod)

    controller = FakeAudioController()
    bot = _build_bot(controller)
    command = bot.get_command("p")
    assert command is not None

    ctx = FakeCtx(author=FakeMember(is_mod=True))

    asyncio.run(command.callback(ctx))

    assert ctx.sent_messages == [
        "Queue is empty. Drop a Suno URL or use ;playlist <Suno Playlist URL>."
    ]
    assert controller.play_next_calls == 0
