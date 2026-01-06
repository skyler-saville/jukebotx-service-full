# packages/core/jukebotx_core/use_cases/get_next_track.py
from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from jukebotx_core.ports.repositories import QueueItem, QueueRepository, Track, TrackRepository


@dataclass(frozen=True)
class NextTrackResult:
    queue_item_id: UUID
    track: Track


class GetNextTrack:
    """
    Get the next unplayed queue item for a guild, then fetch its Track.
    """

    def __init__(self, *, queue_repo: QueueRepository, track_repo: TrackRepository) -> None:
        self._queue_repo = queue_repo
        self._track_repo = track_repo

    async def execute(self, *, guild_id: int, session_id: UUID | None = None) -> NextTrackResult | None:
        qi = await self._queue_repo.get_next_unplayed(guild_id=guild_id, session_id=session_id)
        if qi is None:
            return None

        # We need a way to load by track_id; simplest is add a method, but
        # we can also add it later. For now, assume track_repo can resolve URL-only.
        # If you haven't implemented get_by_id yet, add it to the port and repos.
        track = await self._track_repo.get_by_id(qi.track_id)  # type: ignore[attr-defined]
        return NextTrackResult(queue_item_id=qi.id, track=track)
