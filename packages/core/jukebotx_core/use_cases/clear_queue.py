# packages/core/jukebotx_core/use_cases/clear_queue.py
from __future__ import annotations

from uuid import UUID

from jukebotx_core.ports.repositories import QueueRepository


class ClearQueue:
    """
    Clear a guild queue.
    """

    def __init__(self, *, queue_repo: QueueRepository) -> None:
        self._queue_repo = queue_repo

    async def execute(self, *, guild_id: int, session_id: UUID | None = None) -> None:
        await self._queue_repo.clear(guild_id=guild_id, session_id=session_id)
