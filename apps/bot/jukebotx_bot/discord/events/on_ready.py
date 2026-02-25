from __future__ import annotations

from discord.ext import commands


class OnReadyEvents(commands.Cog):
    """Ready event hook container."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        handler = getattr(self.bot, "_on_ready_extension_hook", None)
        if handler is not None:
            await handler()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(OnReadyEvents(bot))
