from pathlib import Path
import logging
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

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

from jukebotx_bot.discord.notifications import (
    NotificationOutcome,
    safe_channel_send,
    safe_react,
    send_dm_with_fallback,
)


class _FakeResponse:
    status = 500
    reason = "error"


def _http_exception() -> discord.HTTPException:
    return discord.HTTPException(_FakeResponse(), "boom")


def _forbidden_exception() -> discord.Forbidden:
    return discord.Forbidden(_FakeResponse(), "forbidden")


@pytest.mark.asyncio
async def test_send_dm_with_fallback_success() -> None:
    user = SimpleNamespace(
        id=123,
        mention="<@123>",
        send=AsyncMock(return_value=None),
    )
    fallback_channel = SimpleNamespace(send=AsyncMock(return_value=None))

    outcome = await send_dm_with_fallback(
        logger=logging.getLogger("test.notifications"),
        event_name="submission_feedback",
        user=user,
        dm_content="hello",
        guild_id=1,
        channel_id=2,
        fallback_channel=fallback_channel,
    )

    assert outcome == NotificationOutcome.SUCCESS
    user.send.assert_awaited_once_with("hello")
    fallback_channel.send.assert_not_called()


@pytest.mark.asyncio
async def test_send_dm_with_fallback_fallback_on_forbidden(caplog: pytest.LogCaptureFixture) -> None:
    user = SimpleNamespace(
        id=123,
        mention="<@123>",
        send=AsyncMock(side_effect=_forbidden_exception()),
    )
    fallback_channel = SimpleNamespace(send=AsyncMock(return_value=None))

    with caplog.at_level(logging.WARNING):
        outcome = await send_dm_with_fallback(
            logger=logging.getLogger("test.notifications"),
            event_name="submission_feedback",
            user=user,
            dm_content="hello",
            guild_id=1,
            channel_id=2,
            fallback_channel=fallback_channel,
        )

    assert outcome == NotificationOutcome.FALLBACK
    fallback_channel.send.assert_awaited_once_with("<@123> hello")
    assert any(record.event_name == "submission_feedback_dm_forbidden" for record in caplog.records)


@pytest.mark.asyncio
async def test_send_dm_with_fallback_failed_when_dm_and_channel_fail(
    caplog: pytest.LogCaptureFixture,
) -> None:
    user = SimpleNamespace(
        id=123,
        mention="<@123>",
        send=AsyncMock(side_effect=_http_exception()),
    )
    fallback_channel = SimpleNamespace(send=AsyncMock(side_effect=_http_exception()))

    with caplog.at_level(logging.WARNING):
        outcome = await send_dm_with_fallback(
            logger=logging.getLogger("test.notifications"),
            event_name="submission_feedback",
            user=user,
            dm_content="hello",
            guild_id=1,
            channel_id=2,
            fallback_channel=fallback_channel,
        )

    assert outcome == NotificationOutcome.FAILED
    assert any(record.event_name == "submission_feedback_dm_failed" for record in caplog.records)
    assert any(record.event_name == "submission_feedback_channel_send_failed" for record in caplog.records)
    assert any(record.event_name == "submission_feedback_delivery_failed" for record in caplog.records)


@pytest.mark.asyncio
async def test_safe_helpers_return_false_and_log(caplog: pytest.LogCaptureFixture) -> None:
    message = SimpleNamespace(
        id=99,
        add_reaction=AsyncMock(side_effect=_http_exception()),
    )
    channel = SimpleNamespace(send=AsyncMock(side_effect=_http_exception()))

    with caplog.at_level(logging.WARNING):
        sent = await safe_channel_send(
            logger=logging.getLogger("test.notifications"),
            event_name="channel_notice",
            channel=channel,
            content="notice",
            guild_id=1,
            channel_id=2,
            user_id=3,
        )
        reacted = await safe_react(
            logger=logging.getLogger("test.notifications"),
            event_name="reaction_notice",
            message=message,
            emoji="🤘",
            guild_id=1,
            channel_id=2,
            user_id=3,
        )

    assert sent is False
    assert reacted is False
    assert any(record.event_name == "channel_notice_channel_send_failed" for record in caplog.records)
    assert any(record.event_name == "reaction_notice_reaction_failed" for record in caplog.records)
