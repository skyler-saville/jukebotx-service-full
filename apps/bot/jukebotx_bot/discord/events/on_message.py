from __future__ import annotations

import discord
from discord.ext import commands


class OnMessageEvents(commands.Cog):
    """Message event hook container."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        # Keep behavior centralized on the bot implementation.
        handler = getattr(self.bot, "_on_message_extension_hook", None)
        if handler is not None:
            await handler(message)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(OnMessageEvents(bot))
