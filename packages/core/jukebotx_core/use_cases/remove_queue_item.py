from __future__ import annotations

from uuid import UUID

from jukebotx_core.ports.repositories import QueueRepository


class RemoveQueueItem:
    """Remove a queue item from a guild queue."""

    def __init__(self, *, queue_repo: QueueRepository) -> None:
        self._queue_repo = queue_repo

    async def execute(self, *, guild_id: int, queue_item_id: UUID) -> None:
        await self._queue_repo.remove(guild_id=guild_id, queue_item_id=queue_item_id)
