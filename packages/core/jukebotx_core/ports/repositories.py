# packages/core/jukebotx_core/ports/repositories.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class Track:
    """
    A unique track keyed by Suno URL.
    """
    id: UUID
    suno_url: str
    title: str | None
    artist_display: str | None
    artist_username: str | None
    lyrics: str | None
    image_url: str | None
    video_url: str | None
    mp3_url: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class Submission:
    """
    A track being submitted in a specific guild/channel/message context.
    """
    id: UUID
    track_id: UUID
    guild_id: int
    channel_id: int
    message_id: int
    author_id: int
    submitted_at: datetime


@dataclass(frozen=True)
class QueueItem:
    """
    Guild-scoped queue item; "played" is per guild, not global.
    """
    id: UUID
    guild_id: int
    track_id: UUID
    requested_by: int
    status: str  # "queued" | "playing" | "played" | "skipped"
    position: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class TrackUpsert:
    suno_url: str
    title: str | None
    artist_display: str | None
    artist_username: str | None
    lyrics: str | None
    image_url: str | None
    video_url: str | None
    mp3_url: str | None


@dataclass(frozen=True)
class SubmissionCreate:
    track_id: UUID
    guild_id: int
    channel_id: int
    message_id: int
    author_id: int


@dataclass(frozen=True)
class QueueItemCreate:
    guild_id: int
    track_id: UUID
    requested_by: int


class TrackRepository:
    async def get_by_suno_url(self, suno_url: str) -> Track | None:
        raise NotImplementedError

    async def upsert(self, data: TrackUpsert) -> Track:
        raise NotImplementedError


class SubmissionRepository:
    async def get_first_submission_for_track_in_guild(self, *, guild_id: int, track_id: UUID) -> Submission | None:
        """
        Used for 'duplicate within guild' behavior (your old logic).
        """
        raise NotImplementedError

    async def create(self, data: SubmissionCreate) -> Submission:
        raise NotImplementedError


class QueueRepository:
    async def enqueue(self, data: QueueItemCreate) -> QueueItem:
        raise NotImplementedError

    async def get_next_unplayed(self, *, guild_id: int) -> QueueItem | None:
        raise NotImplementedError

    async def mark_played(self, *, guild_id: int, queue_item_id: UUID) -> None:
        raise NotImplementedError

    async def preview(self, *, guild_id: int, limit: int) -> list[QueueItem]:
        raise NotImplementedError

    async def clear(self, *, guild_id: int) -> None:
        raise NotImplementedError
