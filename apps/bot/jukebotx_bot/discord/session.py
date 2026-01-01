# apps/bot/jukebotx_bot/discord/session.py
from __future__ import annotations

from dataclasses import dataclass, field
import time
from uuid import UUID


@dataclass
class Track:
    track_id: UUID | None
    audio_url: str
    page_url: str | None
    title: str
    artist_display: str | None
    media_url: str | None
    gif_url: str | None
    requester_id: int
    requester_name: str


@dataclass
class SessionState:
    submissions_open: bool = True
    per_user_limit: int | None = None
    per_user_counts: dict[int, int] = field(default_factory=dict)
    submission_cooldown_seconds: int = 30
    last_submission_at: dict[int, float] = field(default_factory=dict)
    autoplay_enabled: bool = False
    autoplay_remaining: int | None = None
    dj_enabled: bool = False
    dj_remaining: int | None = None
    queue: list[Track] = field(default_factory=list)
    now_playing: Track | None = None
    now_playing_started_at: float | None = None
    now_playing_channel_id: int | None = None

    def reset(self) -> None:
        self.submissions_open = True
        self.per_user_limit = None
        self.per_user_counts.clear()
        self.last_submission_at.clear()
        self.autoplay_enabled = False
        self.autoplay_remaining = None
        self.dj_enabled = False
        self.dj_remaining = None
        self.queue.clear()
        self.now_playing_channel_id = None
        self.stop_playback()

    def collect_gif_cleanup(self) -> list[tuple[UUID, str]]:
        items: list[tuple[UUID, str]] = []
        for track in self.queue:
            if track.gif_url and track.track_id:
                items.append((track.track_id, track.gif_url))
        if self.now_playing and self.now_playing.gif_url and self.now_playing.track_id:
            items.append((self.now_playing.track_id, self.now_playing.gif_url))
        return items

    def stop_playback(self) -> None:
        self.now_playing = None
        self.now_playing_started_at = None

    def reset_submission_counts(self) -> None:
        self.per_user_counts.clear()
        self.last_submission_at.clear()

    def cooldown_remaining(self, user_id: int, *, now: float | None = None) -> float:
        if self.submission_cooldown_seconds <= 0:
            return 0.0

        last = self.last_submission_at.get(user_id)
        if last is None:
            return 0.0

        current = now if now is not None else time.monotonic()
        remaining = self.submission_cooldown_seconds - (current - last)
        return max(0.0, remaining)

    def mark_submission(self, user_id: int, *, now: float | None = None) -> None:
        current = now if now is not None else time.monotonic()
        self.last_submission_at[user_id] = current

    def set_autoplay(self, remaining: int | None) -> None:
        if remaining is None:
            self.autoplay_enabled = True
            self.autoplay_remaining = None
        else:
            self.autoplay_enabled = remaining > 0
            self.autoplay_remaining = max(remaining, 0)

    def disable_autoplay(self) -> None:
        self.autoplay_enabled = False
        self.autoplay_remaining = None

    def set_dj(self, remaining: int | None) -> None:
        if remaining is None:
            self.dj_enabled = True
            self.dj_remaining = None
        else:
            self.dj_enabled = remaining > 0
            self.dj_remaining = max(remaining, 0)

    def disable_dj(self) -> None:
        self.dj_enabled = False
        self.dj_remaining = None

    def start_next_track(self) -> Track | None:
        if not self.queue:
            self.stop_playback()
            return None

        track = self.queue.pop(0)
        self.now_playing = track
        self.now_playing_started_at = time.monotonic()

        if self.autoplay_enabled and self.autoplay_remaining is not None:
            self.autoplay_remaining -= 1
            if self.autoplay_remaining <= 0:
                self.disable_autoplay()

        if self.dj_enabled and self.dj_remaining is not None:
            self.dj_remaining -= 1
            if self.dj_remaining <= 0:
                self.disable_dj()

        return track


class SessionManager:
    def __init__(self) -> None:
        self._sessions: dict[int, SessionState] = {}

    def for_guild(self, guild_id: int) -> SessionState:
        if guild_id not in self._sessions:
            self._sessions[guild_id] = SessionState()
        return self._sessions[guild_id]
