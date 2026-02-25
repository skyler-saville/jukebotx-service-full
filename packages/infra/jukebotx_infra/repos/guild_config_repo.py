from __future__ import annotations

from dataclasses import replace

from jukebotx_core.ports.repositories import GuildConfig, GuildConfigRepository


class InMemoryGuildConfigRepository(GuildConfigRepository):
    def __init__(self) -> None:
        self._items: dict[int, GuildConfig] = {}

    async def get(self, *, guild_id: int) -> GuildConfig | None:
        return self._items.get(guild_id)

    async def upsert(
        self,
        *,
        guild_id: int,
        submission_cooldown_seconds: int | None = None,
        cooldown_mode: str | None = None,
        autoplay_enabled: bool | None = None,
        autoplay_remaining: int | None = None,
        dj_enabled: bool | None = None,
        dj_remaining: int | None = None,
    ) -> GuildConfig:
        existing = self._items.get(guild_id)
        if existing is None:
            existing = GuildConfig(
                guild_id=guild_id,
                submission_cooldown_seconds=15 * 60,
                cooldown_mode="time",
                autoplay_enabled=False,
                autoplay_remaining=None,
                dj_enabled=False,
                dj_remaining=None,
            )

        updated = replace(
            existing,
            submission_cooldown_seconds=submission_cooldown_seconds
            if submission_cooldown_seconds is not None
            else existing.submission_cooldown_seconds,
            cooldown_mode=cooldown_mode if cooldown_mode is not None else existing.cooldown_mode,
            autoplay_enabled=autoplay_enabled if autoplay_enabled is not None else existing.autoplay_enabled,
            autoplay_remaining=autoplay_remaining if autoplay_enabled is not None or autoplay_remaining is not None else existing.autoplay_remaining,
            dj_enabled=dj_enabled if dj_enabled is not None else existing.dj_enabled,
            dj_remaining=dj_remaining if dj_enabled is not None or dj_remaining is not None else existing.dj_remaining,
        )
        self._items[guild_id] = updated
        return updated
