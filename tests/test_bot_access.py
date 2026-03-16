from __future__ import annotations

from dataclasses import dataclass

import discord

from jukebotx_bot.main import _has_mod_access, _is_master_user


@dataclass(frozen=True)
class _Role:
    name: str


class _Member:
    def __init__(
        self,
        *,
        user_id: int,
        admin: bool = False,
        manage_guild: bool = False,
        roles: list[str] | None = None,
    ) -> None:
        self.id = user_id
        self.guild_permissions = discord.Permissions(administrator=admin, manage_guild=manage_guild)
        self.roles = [_Role(name=role_name) for role_name in (roles or [])]


def test_is_master_user_matches_configured_user_id() -> None:
    assert _is_master_user(user_id=42, master_user_id=42) is True
    assert _is_master_user(user_id=7, master_user_id=42) is False
    assert _is_master_user(user_id=42, master_user_id=None) is False


def test_has_mod_access_allows_master_user_without_roles() -> None:
    member = _Member(user_id=42)

    assert _has_mod_access(member, master_user_id=42) is True


def test_has_mod_access_keeps_existing_mod_logic_for_other_users() -> None:
    role_mod = _Member(user_id=7, roles=["DJ"])
    plain_user = _Member(user_id=8)

    assert _has_mod_access(role_mod, master_user_id=42) is True
    assert _has_mod_access(plain_user, master_user_id=42) is False
