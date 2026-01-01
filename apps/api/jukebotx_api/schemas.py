from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class TrackSummary(BaseModel):
    id: UUID
    suno_url: str
    title: str | None
    artist_display: str | None
    artist_username: str | None
    image_url: str | None
    video_url: str | None
    mp3_url: str | None

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


class SessionTrackResponse(BaseModel):
    track_id: UUID
    artist_display: str | None
    title: str | None
    suno_url: str
    mp3_url: str | None

