from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from jukebotx_core.ports.repositories import QueueItem, QueueItemCreate, QueueRepository
from jukebotx_infra.db.models import QueueItemModel


def _now() -> datetime:
    """Return the current UTC time."""
    return datetime.now(timezone.utc)


def _to_domain(item: QueueItemModel) -> QueueItem:
    """Convert a QueueItemModel to a QueueItem domain object."""
    return QueueItem(
        id=item.id,
        guild_id=item.guild_id,
        track_id=item.track_id,
        requested_by=item.requested_by,
        status=item.status,
        position=item.position,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


class PostgresQueueRepository(QueueRepository):
    """Postgres-backed repository for queue items."""

    def __init__(self, session_factory: async_sessionmaker) -> None:
        """Initialize the repository with an async session factory."""
        self._session_factory = session_factory

    async def enqueue(self, data: QueueItemCreate) -> QueueItem:
        """Add a new queue item for a guild."""
        async with self._session_factory() as session:
            max_pos = await session.scalar(
                select(func.max(QueueItemModel.position)).where(QueueItemModel.guild_id == data.guild_id)
            )
            next_pos = (max_pos or 0) + 1
            now = _now()
            created = QueueItemModel(
                guild_id=data.guild_id,
                track_id=data.track_id,
                requested_by=data.requested_by,
                status="queued",
                position=next_pos,
                created_at=now,
                updated_at=now,
            )
            session.add(created)
            await session.commit()
            await session.refresh(created)
            return _to_domain(created)

    async def get_next_unplayed(self, *, guild_id: int) -> QueueItem | None:
        """Fetch the next queued item for a guild."""
        async with self._session_factory() as session:
            result = await session.scalar(
                select(QueueItemModel)
                .where(QueueItemModel.guild_id == guild_id, QueueItemModel.status == "queued")
                .order_by(QueueItemModel.position.asc())
                .limit(1)
            )
            return _to_domain(result) if result else None

    async def mark_played(self, *, guild_id: int, queue_item_id: UUID) -> None:
        """Mark a queue item as played."""
        async with self._session_factory() as session:
            result = await session.execute(
                update(QueueItemModel)
                .where(QueueItemModel.guild_id == guild_id, QueueItemModel.id == queue_item_id)
                .values(status="played", updated_at=_now())
            )
            await session.commit()
            if result.rowcount == 0:
                raise KeyError(f"Queue item not found: {queue_item_id}")

    async def preview(self, *, guild_id: int, limit: int) -> list[QueueItem]:
        """Return a preview list of queued items for a guild."""
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(QueueItemModel)
                .where(QueueItemModel.guild_id == guild_id, QueueItemModel.status == "queued")
                .order_by(QueueItemModel.position.asc())
                .limit(limit)
            )
            return [_to_domain(item) for item in rows]

    async def clear(self, *, guild_id: int) -> None:
        """Clear all queued items for a guild."""
        async with self._session_factory() as session:
            await session.execute(delete(QueueItemModel).where(QueueItemModel.guild_id == guild_id))
            await session.commit()
