from __future__ import annotations

from dataclasses import dataclass

from jukebotx_core.ports.repositories import GuildConfig, GuildConfigRepository


@dataclass(frozen=True)
class SetGuildConfigInput:
    guild_id: int
    submission_cooldown_seconds: int | None = None
    cooldown_mode: str | None = None
    autoplay_enabled: bool | None = None
    autoplay_remaining: int | None = None
    dj_enabled: bool | None = None
    dj_remaining: int | None = None


@dataclass(frozen=True)
class SetGuildConfigResult:
    config: GuildConfig


class SetGuildConfig:
    """Update guild configuration values."""

    def __init__(self, *, guild_config_repo: GuildConfigRepository) -> None:
        self._guild_config_repo = guild_config_repo

    async def execute(self, data: SetGuildConfigInput) -> SetGuildConfigResult:
        config = await self._guild_config_repo.upsert(
            guild_id=data.guild_id,
            submission_cooldown_seconds=data.submission_cooldown_seconds,
            cooldown_mode=data.cooldown_mode,
            autoplay_enabled=data.autoplay_enabled,
            autoplay_remaining=data.autoplay_remaining,
            dj_enabled=data.dj_enabled,
            dj_remaining=data.dj_remaining,
        )
        return SetGuildConfigResult(config=config)
