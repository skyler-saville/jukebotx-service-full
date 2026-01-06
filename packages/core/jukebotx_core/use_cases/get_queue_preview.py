# packages/core/jukebotx_core/use_cases/get_queue_preview.py
from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from jukebotx_core.ports.repositories import QueueItem, QueueRepository


@dataclass(frozen=True)
class QueuePreviewResult:
    items: list[QueueItem]


class GetQueuePreview:
    """
    Return upcoming queue items for a guild.
    """

    def __init__(self, *, queue_repo: QueueRepository) -> None:
        self._queue_repo = queue_repo

    async def execute(self, *, guild_id: int, session_id: UUID | None = None, limit: int = 5) -> QueuePreviewResult:
        items = await self._queue_repo.preview(guild_id=guild_id, session_id=session_id, limit=limit)
        return QueuePreviewResult(items=items)
