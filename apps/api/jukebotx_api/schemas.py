from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class TrackSummary(BaseModel):
    id: UUID
    suno_url: str
    title: str | None
    artist_display: str | None
    artist_username: str | None
    image_url: str | None
    video_url: str | None
    mp3_url: str | None
    opus_url: str | None
    opus_path: str | None
    opus_status: str | None
    opus_transcoded_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class QueueItemSummary(BaseModel):
    id: UUID
    position: int
    status: str
    requested_by: int
    created_at: datetime
    updated_at: datetime
    track: TrackSummary

    model_config = ConfigDict(from_attributes=True)


class QueuePreviewResponse(BaseModel):
    items: list[QueueItemSummary]


class NextQueueItemResponse(BaseModel):
    queue_item: QueueItemSummary | None


class EnqueueTrackRequest(BaseModel):
    track_id: UUID


class QueueMutationResponse(BaseModel):
    ok: bool = True


class SessionOpenRequest(BaseModel):
    is_open: bool


class SessionTrackLimitRequest(BaseModel):
    track_limit: int | None = Field(default=None, ge=1)


class SessionAutoplayRequest(BaseModel):
    enabled: bool
    remaining: int | None = Field(default=None, ge=1)


class SessionDjRequest(BaseModel):
    enabled: bool
    remaining: int | None = Field(default=None, ge=1)


class SessionCooldownRequest(BaseModel):
    mode: str
    seconds: int = Field(ge=0)


class GuildConfigResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    guild_id: int
    session_open: bool
    session_track_limit: int | None
    submission_cooldown_seconds: int
    cooldown_mode: str
    autoplay_enabled: bool
    autoplay_remaining: int | None
    dj_enabled: bool
    dj_remaining: int | None


class SessionTrackResponse(BaseModel):
    track_id: UUID
    artist_display: str | None
    title: str | None
    suno_url: str
    mp3_url: str | None


class OpusStatusResponse(BaseModel):
    track_id: UUID
    ready: bool
    status: str
