# packages/core/jukebotx_core/ports/repositories.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
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
    opus_url: str | None
    opus_path: str | None
    opus_status: str | None
    opus_transcoded_at: datetime | None
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
class SubmissionTrackInfo:
    """
    Track details joined to a submission for setlist exports.
    """
    track_id: UUID
    artist_display: str | None
    title: str | None
    suno_url: str
    mp3_url: str | None


@dataclass(frozen=True)
class QueueItem:
    """
    Guild-scoped queue item; "played" is per guild, not global.
    """
    id: UUID
    guild_id: int
    session_id: UUID | None
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
    opus_url: str | None = None
    opus_path: str | None = None
    opus_status: str | None = None
    opus_transcoded_at: datetime | None = None


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
    session_id: UUID | None = None


class JamSessionStatus(Enum):
    ACTIVE = "active"
    ENDED = "ended"


class SessionReactionType(Enum):
    UPVOTE = "upvote"
    DOWNVOTE = "downvote"


@dataclass(frozen=True)
class JamSession:
    id: UUID
    guild_id: int
    channel_id: int
    status: JamSessionStatus
    created_at: datetime
    updated_at: datetime
    ended_at: datetime | None


@dataclass(frozen=True)
class JamSessionCreate:
    guild_id: int
    channel_id: int
    status: JamSessionStatus = JamSessionStatus.ACTIVE


@dataclass(frozen=True)
class SessionReaction:
    id: UUID
    session_id: UUID
    track_id: UUID
    user_id: int
    reaction_type: SessionReactionType
    created_at: datetime


@dataclass(frozen=True)
class SessionReactionCreate:
    session_id: UUID
    track_id: UUID
    user_id: int
    reaction_type: SessionReactionType


@dataclass(frozen=True)
class OpusJob:
    id: UUID
    track_id: UUID
    mp3_url: str
    status: str
    error: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class OpusJobCreate:
    track_id: UUID
    mp3_url: str


class TrackRepository:
    async def get_by_suno_url(self, suno_url: str) -> Track | None:
        raise NotImplementedError

    async def upsert(self, data: TrackUpsert) -> Track:
        raise NotImplementedError

    async def update_opus_metadata(
        self,
        *,
        track_id: UUID,
        opus_url: str | None,
        opus_path: str | None,
        opus_status: str | None,
        opus_transcoded_at: datetime | None,
    ) -> Track:
        raise NotImplementedError


class SubmissionRepository:
    async def get_first_submission_for_track_in_guild(self, *, guild_id: int, track_id: UUID) -> Submission | None:
        """
        Used for 'duplicate within guild' behavior (your old logic).
        """
        raise NotImplementedError

    async def create(self, data: SubmissionCreate) -> Submission:
        raise NotImplementedError


class OpusJobRepository:
    async def get_by_track_id(self, *, track_id: UUID) -> OpusJob | None:
        raise NotImplementedError

    async def enqueue(self, data: OpusJobCreate) -> OpusJob:
        raise NotImplementedError

    async def fetch_next_pending(self) -> OpusJob | None:
        raise NotImplementedError

    async def mark_completed(self, *, job_id: UUID) -> None:
        raise NotImplementedError

    async def mark_failed(self, *, job_id: UUID, error: str) -> None:
        raise NotImplementedError


class QueueRepository:
    async def enqueue(self, data: QueueItemCreate) -> QueueItem:
        raise NotImplementedError

    async def get_next_unplayed(self, *, guild_id: int, session_id: UUID | None) -> QueueItem | None:
        raise NotImplementedError

    async def mark_played(self, *, guild_id: int, session_id: UUID | None, queue_item_id: UUID) -> None:
        raise NotImplementedError

    async def preview(self, *, guild_id: int, session_id: UUID | None, limit: int) -> list[QueueItem]:
        raise NotImplementedError

    async def clear(self, *, guild_id: int, session_id: UUID | None) -> None:
        raise NotImplementedError


class JamSessionRepository:
    async def create(self, data: JamSessionCreate) -> JamSession:
        raise NotImplementedError

    async def get_by_id(self, *, session_id: UUID) -> JamSession | None:
        raise NotImplementedError

    async def get_active_for_guild(self, *, guild_id: int) -> JamSession | None:
        raise NotImplementedError

    async def end(self, *, session_id: UUID) -> JamSession:
        raise NotImplementedError


class SessionReactionRepository:
    async def add(self, data: SessionReactionCreate) -> SessionReaction:
        raise NotImplementedError

    async def list_for_session(self, *, session_id: UUID) -> list[SessionReaction]:
        raise NotImplementedError

    async def remove(
        self,
        *,
        session_id: UUID,
        track_id: UUID,
        user_id: int,
        reaction_type: SessionReactionType,
    ) -> None:
        raise NotImplementedError
