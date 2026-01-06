from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from jukebotx_core.ports.repositories import JamSession, JamSessionCreate, JamSessionRepository, JamSessionStatus
from jukebotx_infra.db.models import JamSessionModel, JamSessionStatus as JamSessionStatusModel


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _to_domain(session: JamSessionModel) -> JamSession:
    return JamSession(
        id=session.id,
        guild_id=session.guild_id,
        channel_id=session.channel_id,
        status=JamSessionStatus(session.status.value),
        created_at=session.created_at,
        updated_at=session.updated_at,
        ended_at=session.ended_at,
    )


class PostgresJamSessionRepository(JamSessionRepository):
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def create(self, data: JamSessionCreate) -> JamSession:
        async with self._session_factory() as session:
            now = _now()
            created = JamSessionModel(
                guild_id=data.guild_id,
                channel_id=data.channel_id,
                status=JamSessionStatusModel(data.status.value),
                created_at=now,
                updated_at=now,
            )
            session.add(created)
            await session.commit()
            await session.refresh(created)
            return _to_domain(created)

    async def get_by_id(self, *, session_id: UUID) -> JamSession | None:
        async with self._session_factory() as session:
            result = await session.get(JamSessionModel, session_id)
            return _to_domain(result) if result else None

    async def get_active_for_guild(self, *, guild_id: int) -> JamSession | None:
        async with self._session_factory() as session:
            result = await session.scalar(
                select(JamSessionModel).where(
                    JamSessionModel.guild_id == guild_id,
                    JamSessionModel.ended_at.is_(None),
                )
            )
            return _to_domain(result) if result else None

    async def end(self, *, session_id: UUID) -> JamSession:
        async with self._session_factory() as session:
            result = await session.execute(
                update(JamSessionModel)
                .where(JamSessionModel.id == session_id)
                .values(
                    status=JamSessionStatusModel.ENDED,
                    ended_at=_now(),
                    updated_at=_now(),
                )
                .returning(JamSessionModel)
            )
            row = result.scalar_one_or_none()
            await session.commit()
            if row is None:
                raise KeyError(f"Jam session not found: {session_id}")
            return _to_domain(row)
