# apps/bot/jukebotx_bot/discord/session.py
from __future__ import annotations

from dataclasses import dataclass, field
import time


@dataclass
class Track:
    audio_url: str
    page_url: str | None
    title: str
    requester_id: int
    requester_name: str


@dataclass
class SessionState:
    submissions_open: bool = True
    per_user_limit: int | None = None
    per_user_counts: dict[int, int] = field(default_factory=dict)
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
        self.autoplay_enabled = False
        self.autoplay_remaining = None
        self.dj_enabled = False
        self.dj_remaining = None
        self.queue.clear()
        self.now_playing_channel_id = None
        self.stop_playback()

    def stop_playback(self) -> None:
        self.now_playing = None
        self.now_playing_started_at = None

    def reset_submission_counts(self) -> None:
        self.per_user_counts.clear()

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
