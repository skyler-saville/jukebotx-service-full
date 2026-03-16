from __future__ import annotations

from datetime import datetime
from typing import Literal
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
    web_audio_url: str | None
    web_audio_path: str | None
    web_audio_status: str | None
    web_audio_transcoded_at: datetime | None

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
    web_audio_url: str | None = None
    web_audio_status: str | None = None


class WebSessionCurrentTrackResponse(BaseModel):
    track_id: UUID
    artist_display: str | None
    artist_username: str | None
    title: str | None
    lyrics: str | None
    suno_url: str
    web_audio_status: str | None
    image_url: str | None
    video_url: str | None


class WebSessionQueueItemResponse(BaseModel):
    queue_item_id: UUID
    position: int
    track_id: UUID
    artist_display: str | None
    artist_username: str | None
    title: str | None
    suno_url: str
    image_url: str | None
    web_audio_status: str | None


class WebSessionResponse(BaseModel):
    session_id: UUID
    guild_id: int
    channel_id: int
    is_active: bool
    status: Literal["live", "waiting", "offline"]
    activated_at: datetime | None
    ended_at: datetime | None
    current_audio_url: str | None = None
    current_track: WebSessionCurrentTrackResponse | None
    queue: list[WebSessionQueueItemResponse] = Field(default_factory=list)


class ActivateWebSessionRequest(BaseModel):
    current_track_id: UUID | None = None



class OpusStatusResponse(BaseModel):
    track_id: UUID
    ready: bool
    status: str


class WebAudioStatusResponse(BaseModel):
    track_id: UUID
    ready: bool
    status: str
