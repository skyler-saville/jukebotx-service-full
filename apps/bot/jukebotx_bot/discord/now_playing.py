from __future__ import annotations

import time

import discord

from jukebotx_bot.discord.session import Track


def _format_duration(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    minutes, remaining_seconds = divmod(total_seconds, 60)
    return f"{minutes}:{remaining_seconds:02d}"


def _build_progress_bar(elapsed_seconds: float, duration_seconds: float, *, width: int = 16) -> str:
    if duration_seconds <= 0:
        return "[" + ("-" * width) + "]"

    ratio = min(max(elapsed_seconds / duration_seconds, 0.0), 1.0)
    filled = min(width, max(0, round(ratio * width)))
    return "[" + ("=" * filled) + ("-" * (width - filled)) + "]"


def build_now_playing_embed(
    track: Track,
    *,
    started_at: float | None = None,
    now: float | None = None,
) -> discord.Embed:
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
        embed.add_field(
            name="⏱️ Duration",
            value=_format_duration(duration),
            inline=True,
        )

        if started_at is not None:
            current_time = now if now is not None else time.monotonic()
            elapsed = min(max(current_time - started_at, 0.0), duration)
            embed.add_field(
                name="▶️ Progress",
                value=f"{_build_progress_bar(elapsed, duration)} {_format_duration(elapsed)} / {_format_duration(duration)}",
                inline=False,
            )

    return embed
