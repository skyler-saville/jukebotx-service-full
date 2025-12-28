from __future__ import annotations

import discord

from jukebotx_bot.discord.session import Track


def build_now_playing_embed(track: Track) -> discord.Embed:
    title = track.title or "🎵 Now Playing"
    artist = None
    media_url = None
    url = track.page_url or track.audio_url

    embed = discord.Embed(
        title=title or "🎵 Now Playing",
        description=f"By **{artist}**" if artist else "Unknown Artist",
        color=0x1DB954,
    )

    if media_url:
        embed.set_image(url=media_url)

    if url:
        embed.add_field(
            name="🔗 Original Link",
            value=f"[Listen on Suno]({url})",
            inline=False,
        )

    return embed
