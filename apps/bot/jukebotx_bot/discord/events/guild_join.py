from __future__ import annotations

import discord
from discord.ext import commands


class GuildJoinEvents(commands.Cog):
    """Guild join event hook container."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        handler = getattr(self.bot, "_on_guild_join_extension_hook", None)
        if handler is not None:
            await handler(guild)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(GuildJoinEvents(bot))
