# packages/core/jukebotx_core/use_cases/mark_track_played.py
from __future__ import annotations

from uuid import UUID

from jukebotx_core.ports.repositories import QueueRepository


class MarkTrackPlayed:
    """
    Mark a queue item as played for a specific guild.
    """

    def __init__(self, *, queue_repo: QueueRepository) -> None:
        self._queue_repo = queue_repo

    async def execute(self, *, guild_id: int, queue_item_id: UUID, session_id: UUID | None = None) -> None:
        await self._queue_repo.mark_played(guild_id=guild_id, session_id=session_id, queue_item_id=queue_item_id)
