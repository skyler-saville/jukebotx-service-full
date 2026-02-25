import asyncio
from types import SimpleNamespace
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.extend(
    [
        str(ROOT / "apps" / "bot"),
        str(ROOT / "packages" / "core"),
        str(ROOT / "packages" / "infra"),
    ]
)

import discord

from jukebotx_bot.main import BotDeps, JukeBot


class FakeCtx:
    def __init__(self) -> None:
        self.sent_messages: list[str] = []

    async def send(self, content=None, embed=None):
        self.sent_messages.append(content if content is not None else "<embed>")


def _build_bot() -> JukeBot:
    settings = SimpleNamespace(env="development", opus_api_base_url=None)
    deps = BotDeps(
        session_manager=SimpleNamespace(),
        ingest_use_case=None,
        audio_manager=SimpleNamespace(),
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


def test_last_commit_command_sends_commit_date(monkeypatch) -> None:
    from jukebotx_bot import main as main_module

    monkeypatch.setattr(main_module, "_get_last_commit_date", lambda: "2026-01-01 00:00:00 UTC")

    bot = _build_bot()
    command = bot.get_command("last-commit")
    assert command is not None

    ctx = FakeCtx()
    asyncio.run(command.callback(ctx))

    assert ctx.sent_messages == ["Last commit date: 2026-01-01 00:00:00 UTC"]


def test_last_commit_command_handles_missing_git_info(monkeypatch) -> None:
    from jukebotx_bot import main as main_module

    monkeypatch.setattr(main_module, "_get_last_commit_date", lambda: None)

    bot = _build_bot()
    command = bot.get_command("last-commit")
    assert command is not None

    ctx = FakeCtx()
    asyncio.run(command.callback(ctx))

    assert ctx.sent_messages == ["Couldn't determine the last commit date."]
