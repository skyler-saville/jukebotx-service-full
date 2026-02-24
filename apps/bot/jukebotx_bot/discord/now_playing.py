from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

import discord

from jukebotx_bot.discord.session import Track


def _format_duration(duration_seconds: float | None) -> str | None:
    if duration_seconds is None:
        return None

    total_seconds = max(0, int(round(duration_seconds)))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def _embed_image_url(media_url: str | None) -> str | None:
    if not media_url:
        return None

    parsed = urlsplit(media_url)
    path_lower = parsed.path.lower()
    if path_lower.endswith((".jpg", ".png", ".webp", ".gif")):
        return media_url

    if path_lower.endswith(".mp4"):
        gif_path = parsed.path[:-4] + ".gif"
        return urlunsplit(parsed._replace(path=gif_path))

    return None


def build_now_playing_embed(
    track: Track,
    *,
    requester_display: str | None = None,
    queue_remaining: int | None = None,
) -> discord.Embed:
    title = track.title or "🎵 Now Playing"
    artist = track.artist_display or "Unknown Artist"
    url = track.page_url or track.audio_url
    duration_display = _format_duration(track.duration_seconds)
    requester_value = requester_display or track.requester_name
    image_url = _embed_image_url(track.media_url)

    embed = discord.Embed(
        title=title or "🎵 Now Playing",
        description=f"By **{artist}**",
        color=0x1DB954,
    )

    if image_url:
        embed.set_image(url=image_url)

    if url:
        embed.add_field(
            name="🔗 Original Link",
            value=f"[Listen on Suno]({url})",
            inline=False,
        )

    if duration_display is not None:
        embed.add_field(
            name="⏱️ Duration",
            value=duration_display,
            inline=True,
        )

    embed.add_field(name="🙋 Requested by", value=requester_value, inline=True)

    if queue_remaining is not None:
        queue_total = queue_remaining + 1
        next_up_label = "track" if queue_remaining == 1 else "tracks"
        embed.add_field(
            name="📚 Queue",
            value=f"Now playing **1/{queue_total}** · **{queue_remaining}** {next_up_label} up next",
            inline=False,
        )

    return embed
