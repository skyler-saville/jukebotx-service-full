from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from jukebotx_core.ports.repositories import (
    SessionReaction,
    SessionReactionCreate,
    SessionReactionRepository,
    SessionReactionType,
)
from jukebotx_infra.db.models import SessionReactionModel, SessionReactionType as SessionReactionTypeModel


def _to_domain(reaction: SessionReactionModel) -> SessionReaction:
    return SessionReaction(
        id=reaction.id,
        session_id=reaction.session_id,
        track_id=reaction.track_id,
        user_id=reaction.user_id,
        reaction_type=SessionReactionType(reaction.reaction_type.value),
        created_at=reaction.created_at,
    )


class PostgresSessionReactionRepository(SessionReactionRepository):
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def add(self, data: SessionReactionCreate) -> SessionReaction:
        async with self._session_factory() as session:
            created = SessionReactionModel(
                session_id=data.session_id,
                track_id=data.track_id,
                user_id=data.user_id,
                reaction_type=SessionReactionTypeModel(data.reaction_type.value),
            )
            session.add(created)
            await session.commit()
            await session.refresh(created)
            return _to_domain(created)

    async def list_for_session(self, *, session_id: UUID) -> list[SessionReaction]:
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(SessionReactionModel).where(SessionReactionModel.session_id == session_id)
            )
            return [_to_domain(row) for row in rows]

    async def remove(
        self,
        *,
        session_id: UUID,
        track_id: UUID,
        user_id: int,
        reaction_type: SessionReactionType,
    ) -> None:
        async with self._session_factory() as session:
            result = await session.execute(
                delete(SessionReactionModel).where(
                    SessionReactionModel.session_id == session_id,
                    SessionReactionModel.track_id == track_id,
                    SessionReactionModel.user_id == user_id,
                    SessionReactionModel.reaction_type == SessionReactionTypeModel(reaction_type.value),
                )
            )
            await session.commit()
            if result.rowcount == 0:
                raise KeyError("Session reaction not found.")
