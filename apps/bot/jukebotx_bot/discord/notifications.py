from __future__ import annotations

from enum import StrEnum
import logging

import discord

from jukebotx_bot.logging_config import get_event_logger


class NotificationOutcome(StrEnum):
    SUCCESS = "success"
    FALLBACK = "fallback"
    FAILED = "failed"


async def safe_channel_send(
    *,
    logger: logging.Logger,
    event_name: str,
    channel: discord.abc.Messageable | None,
    content: str,
    guild_id: int | None,
    channel_id: int | None,
    user_id: int | None,
) -> bool:
    if channel is None:
        get_event_logger(
            logger,
            event_name=f"{event_name}_channel_send_skipped",
            guild_id=guild_id,
            channel_id=channel_id,
            user_id=user_id,
            error_type="MissingChannel",
        ).warning("Channel send skipped because channel is not available")
        return False

    try:
        await channel.send(content)
    except discord.HTTPException as exc:
        get_event_logger(
            logger,
            event_name=f"{event_name}_channel_send_failed",
            guild_id=guild_id,
            channel_id=channel_id,
            user_id=user_id,
            error_type=type(exc).__name__,
        ).warning("Channel send failed: %s", exc)
        return False

    return True


async def safe_react(
    *,
    logger: logging.Logger,
    event_name: str,
    message: discord.Message,
    emoji: str,
    guild_id: int | None,
    channel_id: int | None,
    user_id: int | None,
) -> bool:
    try:
        await message.add_reaction(emoji)
    except discord.HTTPException as exc:
        get_event_logger(
            logger,
            event_name=f"{event_name}_reaction_failed",
            guild_id=guild_id,
            channel_id=channel_id,
            user_id=user_id,
            message_id=message.id,
            error_type=type(exc).__name__,
        ).warning("Failed to add reaction %s: %s", emoji, exc)
        return False

    return True


async def send_dm_with_fallback(
    *,
    logger: logging.Logger,
    event_name: str,
    user: discord.abc.User,
    dm_content: str,
    guild_id: int | None,
    channel_id: int | None,
    fallback_channel: discord.abc.Messageable | None,
    fallback_content: str | None = None,
) -> NotificationOutcome:
    user_id = user.id

    try:
        await user.send(dm_content)
        return NotificationOutcome.SUCCESS
    except discord.Forbidden as exc:
        get_event_logger(
            logger,
            event_name=f"{event_name}_dm_forbidden",
            guild_id=guild_id,
            channel_id=channel_id,
            user_id=user_id,
            error_type=type(exc).__name__,
        ).warning("DM forbidden; attempting channel fallback")
    except discord.HTTPException as exc:
        get_event_logger(
            logger,
            event_name=f"{event_name}_dm_failed",
            guild_id=guild_id,
            channel_id=channel_id,
            user_id=user_id,
            error_type=type(exc).__name__,
        ).warning("DM failed; attempting channel fallback: %s", exc)

    mention_fallback = fallback_content or f"{user.mention} {dm_content}"
    sent = await safe_channel_send(
        logger=logger,
        event_name=event_name,
        channel=fallback_channel,
        content=mention_fallback,
        guild_id=guild_id,
        channel_id=channel_id,
        user_id=user_id,
    )
    if sent:
        return NotificationOutcome.FALLBACK

    get_event_logger(
        logger,
        event_name=f"{event_name}_delivery_failed",
        guild_id=guild_id,
        channel_id=channel_id,
        user_id=user_id,
        error_type="NotificationFailed",
    ).warning("Both DM and channel fallback delivery failed")
    return NotificationOutcome.FAILED
