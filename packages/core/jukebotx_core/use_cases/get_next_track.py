# packages/core/jukebotx_core/use_cases/get_next_track.py
from __future__ import annotations

from dataclasses import dataclass

from jukebotx_core.ports.repositories import QueueItem, QueueRepository, Track, TrackRepository


@dataclass(frozen=True)
class NextTrackResult:
    queue_item: QueueItem
    track: Track


class GetNextTrack:
    """
    Get the next unplayed queue item for a guild, then fetch its Track.
    """

    def __init__(self, *, queue_repo: QueueRepository, track_repo: TrackRepository) -> None:
        self._queue_repo = queue_repo
        self._track_repo = track_repo

    async def execute(self, *, guild_id: int) -> NextTrackResult | None:
        qi = await self._queue_repo.get_next_unplayed(guild_id=guild_id)
        if qi is None:
            return None

        track = await self._track_repo.get_by_id(qi.track_id)
        return NextTrackResult(queue_item=qi, track=track)
