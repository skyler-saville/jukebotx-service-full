from __future__ import annotations

from discord.ext import commands


class LibraryCog(commands.Cog):
    """Library and discovery command surface and helpers."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(LibraryCog(bot))
