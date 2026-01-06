from __future__ import annotations

from datetime import datetime
from typing import Generic, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict

SchemaDataT = TypeVar("SchemaDataT")


class ReactionCountDTO(BaseModel):
    track_id: UUID
    reaction_type: str
    count: int

    model_config = ConfigDict(from_attributes=True)


class QueueItemDTO(BaseModel):
    id: UUID
    position: int
    status: str
    requested_by: int
    created_at: datetime
    updated_at: datetime
    track_id: UUID
    title: str | None
    artist_display: str | None
    image_url: str | None
    mp3_url: str | None
    opus_url: str | None

    model_config = ConfigDict(from_attributes=True)


class NowPlayingDTO(BaseModel):
    queue_item: QueueItemDTO | None
    started_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class SessionStateDTO(BaseModel):
    session_id: UUID | None
    guild_id: int
    channel_id: int | None
    status: str | None
    created_at: datetime | None
    updated_at: datetime | None
    ended_at: datetime | None
    now_playing: NowPlayingDTO | None
    queue: list[QueueItemDTO]
    reactions: list[ReactionCountDTO]

    model_config = ConfigDict(from_attributes=True)


class EventEnvelope(BaseModel, Generic[SchemaDataT]):
    schema_version: str = "1.0"
    event_type: str
    data: SchemaDataT

    model_config = ConfigDict(from_attributes=True)
