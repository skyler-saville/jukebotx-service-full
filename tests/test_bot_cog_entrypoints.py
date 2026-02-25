from __future__ import annotations

import discord
import pytest

from jukebotx_bot.main import BotDeps, JukeBot


@pytest.mark.asyncio
async def test_setup_hook_loads_cogs_and_events(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _noop() -> None:
        return None

    settings = type("S", (), {"discord_token": "x"})()
    deps = BotDeps(
        session_manager=type("SessionMgr", (), {})(),
        ingest_use_case=object(),
        audio_manager=type("AudioMgr", (), {})(),
        playlist_client=object(),
        submission_repo=object(),
        queue_repo=object(),
    )

    bot = JukeBot(
        settings=settings,
        deps=deps,
        command_prefix=";",
        intents=discord.Intents.none(),
    )

    monkeypatch.setattr("jukebotx_bot.main.init_db", _noop)
    monkeypatch.setattr(bot, "_auto_leave_loop", _noop)

    await bot.setup_hook()

    assert "QueueCog" in bot.cogs
    assert "ConfigCog" in bot.cogs
    assert "LibraryCog" in bot.cogs
    assert "OnReadyEvents" in bot.cogs
    assert "OnMessageEvents" in bot.cogs
    assert "GuildJoinEvents" in bot.cogs

    await bot.close()
