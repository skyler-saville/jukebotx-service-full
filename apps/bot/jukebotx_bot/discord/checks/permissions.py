# apps/bot/jukebotx_bot/discord/checks/permissions.py
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import discord
from discord.ext import commands

# A callable you provide that returns allowed role IDs for a given guild_id.
AllowedRoleIdsProvider = Callable[[int], Awaitable[set[int]]]


def _is_guild_admin(member: discord.Member) -> bool:
    """
    Return True if the user has administrator permission in the guild.
    """
    return bool(member.guild_permissions.administrator)


def has_allowed_roles(
    allowed_role_ids_provider: AllowedRoleIdsProvider,
    *,
    allow_admin: bool = True,
) -> Callable[[commands.Context[Any]], Awaitable[bool]]:
    """
    Create a discord.py command check that allows a user if they have at least one
    role ID configured for that guild (or if they are an admin and allow_admin=True).

    This is multi-guild safe because the allowed roles are fetched by guild_id.

    Usage:
        @commands.command()
        @commands.check(has_allowed_roles(container.get_dj_role_ids))
        async def skip(ctx): ...

    Args:
        allowed_role_ids_provider: Async function that returns role IDs allowed in a guild.
        allow_admin: Whether guild admins bypass the role requirement.

    Returns:
        A predicate suitable for discord.ext.commands.check(...)
    """

    async def predicate(ctx: commands.Context[Any]) -> bool:
        if ctx.guild is None:
            return False  # no DMs

        if not isinstance(ctx.author, discord.Member):
            return False

        member: discord.Member = ctx.author

        if allow_admin and _is_guild_admin(member):
            return True

        allowed_role_ids = await allowed_role_ids_provider(ctx.guild.id)
        if not allowed_role_ids:
            # If not configured yet, fail closed.
            return False

        member_role_ids = {role.id for role in member.roles}
        return not member_role_ids.isdisjoint(allowed_role_ids)

    return predicate


def has_any_role_ids(
    role_ids: set[int],
    *,
    allow_admin: bool = True,
) -> Callable[[commands.Context[Any]], Awaitable[bool]]:
    """
    Simple check for fixed role IDs (useful for quick tests, not ideal long-term).

    Args:
        role_ids: Role IDs that grant access.
        allow_admin: Whether guild admins bypass the role requirement.
    """

    async def predicate(ctx: commands.Context[Any]) -> bool:
        if ctx.guild is None:
            return False
        if not isinstance(ctx.author, discord.Member):
            return False

        member: discord.Member = ctx.author

        if allow_admin and _is_guild_admin(member):
            return True

        member_role_ids = {role.id for role in member.roles}
        return not member_role_ids.isdisjoint(role_ids)

    return predicate
