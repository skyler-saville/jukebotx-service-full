# packages/infra/jukebotx_infra/repos/memory.py
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from uuid import UUID, uuid4

from jukebotx_core.ports.repositories import (
    QueueItem,
    QueueItemCreate,
    QueueRepository,
    Submission,
    SubmissionCreate,
    SubmissionRepository,
    Track,
    TrackRepository,
    TrackUpsert,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class InMemoryTrackRepository(TrackRepository):
    def __init__(self) -> None:
        self._by_id: dict[UUID, Track] = {}
        self._by_url: dict[str, UUID] = {}

    async def get_by_suno_url(self, suno_url: str) -> Track | None:
        track_id = self._by_url.get(suno_url)
        return self._by_id.get(track_id) if track_id else None

    async def get_by_id(self, track_id: UUID) -> Track:  # add this to port long-term
        t = self._by_id.get(track_id)
        if t is None:
            raise KeyError(f"Track not found: {track_id}")
        return t

    async def upsert(self, data: TrackUpsert) -> Track:
        existing = await self.get_by_suno_url(data.suno_url)
        now = _now()

        if existing:
            updated = replace(
                existing,
                title=data.title or existing.title,
                artist_display=data.artist_display or existing.artist_display,
                artist_username=data.artist_username or existing.artist_username,
                lyrics=data.lyrics or existing.lyrics,
                gif_url=data.gif_url if data.gif_url is not None else existing.gif_url,
                image_url=data.image_url or existing.image_url,
                video_url=data.video_url or existing.video_url,
                mp3_url=data.mp3_url or existing.mp3_url,
                updated_at=now,
            )
            self._by_id[existing.id] = updated
            return updated

        track_id = uuid4()
        track = Track(
            id=track_id,
            suno_url=data.suno_url,
            title=data.title,
            artist_display=data.artist_display,
            artist_username=data.artist_username,
            lyrics=data.lyrics,
            gif_url=data.gif_url,
            image_url=data.image_url,
            video_url=data.video_url,
            mp3_url=data.mp3_url,
            created_at=now,
            updated_at=now,
        )
        self._by_id[track_id] = track
        self._by_url[data.suno_url] = track_id
        return track

    async def update_gif_url(self, *, track_id: UUID, gif_url: str | None) -> Track:
        existing = self._by_id.get(track_id)
        if existing is None:
            raise KeyError(f"Track not found: {track_id}")
        updated = replace(existing, gif_url=gif_url, updated_at=_now())
        self._by_id[track_id] = updated
        return updated


class InMemorySubmissionRepository(SubmissionRepository):
    def __init__(self) -> None:
        self._items: list[Submission] = []

    async def get_first_submission_for_track_in_guild(self, *, guild_id: int, track_id: UUID) -> Submission | None:
        for s in self._items:
            if s.guild_id == guild_id and s.track_id == track_id:
                return s
        return None

    async def create(self, data: SubmissionCreate) -> Submission:
        now = _now()
        s = Submission(
            id=uuid4(),
            track_id=data.track_id,
            guild_id=data.guild_id,
            channel_id=data.channel_id,
            message_id=data.message_id,
            author_id=data.author_id,
            submitted_at=now,
        )
        self._items.append(s)
        return s


class InMemoryQueueRepository(QueueRepository):
    def __init__(self) -> None:
        self._by_guild: dict[int, list[QueueItem]] = {}

    async def enqueue(self, data: QueueItemCreate) -> QueueItem:
        now = _now()
        items = self._by_guild.setdefault(data.guild_id, [])
        position = len(items) + 1

        qi = QueueItem(
            id=uuid4(),
            guild_id=data.guild_id,
            track_id=data.track_id,
            requested_by=data.requested_by,
            status="queued",
            position=position,
            created_at=now,
            updated_at=now,
        )
        items.append(qi)
        return qi

    async def get_next_unplayed(self, *, guild_id: int) -> QueueItem | None:
        items = self._by_guild.get(guild_id, [])
        for qi in items:
            if qi.status == "queued":
                return qi
        return None

    async def mark_played(self, *, guild_id: int, queue_item_id: UUID) -> None:
        items = self._by_guild.get(guild_id, [])
        for idx, qi in enumerate(items):
            if qi.id == queue_item_id:
                items[idx] = replace(qi, status="played", updated_at=_now())
                return
        raise KeyError(f"Queue item not found: {queue_item_id}")

    async def preview(self, *, guild_id: int, limit: int) -> list[QueueItem]:
        items = self._by_guild.get(guild_id, [])
        queued = [qi for qi in items if qi.status == "queued"]
        return queued[:limit]

    async def clear(self, *, guild_id: int) -> None:
        self._by_guild[guild_id] = []
