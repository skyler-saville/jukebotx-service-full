from __future__ import annotations

from dataclasses import dataclass

from jukebotx_core.ports.repositories import QueueItem, QueueRepository


@dataclass(frozen=True)
class ListQueueResult:
    items: list[QueueItem]


class ListQueue:
    """List queued items for a guild."""

    def __init__(self, *, queue_repo: QueueRepository) -> None:
        self._queue_repo = queue_repo

    async def execute(self, *, guild_id: int, limit: int = 50) -> ListQueueResult:
        items = await self._queue_repo.list(guild_id=guild_id, limit=limit)
        return ListQueueResult(items=items)
