from __future__ import annotations

import discord

from jukebotx_bot.discord.session import Track


def build_now_playing_embed(track: Track) -> discord.Embed:
    title = track.title or "🎵 Now Playing"
    artist = track.artist_display or "Unknown Artist"
    media_url = track.media_url
    url = track.page_url or track.audio_url
    duration = track.duration_seconds

    embed = discord.Embed(
        title=title or "🎵 Now Playing",
        description=f"By **{artist}**",
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

    if duration:
        minutes = int(duration // 60)
        seconds = int(duration % 60)
        embed.add_field(
            name="⏱️ Duration",
            value=f"{minutes}:{seconds:02d}",
            inline=True,
        )

    return embed
