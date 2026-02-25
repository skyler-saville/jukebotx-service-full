from __future__ import annotations

from discord.ext import commands


class ConfigCog(commands.Cog):
    """Guild configuration command surface and helpers."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ConfigCog(bot))
