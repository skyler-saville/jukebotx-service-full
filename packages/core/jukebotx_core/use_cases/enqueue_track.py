from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from jukebotx_core.ports.repositories import QueueItem, QueueItemCreate, QueueRepository


@dataclass(frozen=True)
class EnqueueTrackInput:
    guild_id: int
    track_id: UUID
    requested_by: int


@dataclass(frozen=True)
class EnqueueTrackResult:
    item: QueueItem


class EnqueueTrack:
    """Enqueue a track for a guild queue."""

    def __init__(self, *, queue_repo: QueueRepository) -> None:
        self._queue_repo = queue_repo

    async def execute(self, data: EnqueueTrackInput) -> EnqueueTrackResult:
        item = await self._queue_repo.enqueue(
            QueueItemCreate(
                guild_id=data.guild_id,
                track_id=data.track_id,
                requested_by=data.requested_by,
            )
        )
        return EnqueueTrackResult(item=item)
