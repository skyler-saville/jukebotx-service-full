from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from jukebotx_core.ports.repositories import (
    WebSession,
    WebSessionCreate,
    WebSessionRepository,
)
from jukebotx_infra.db.models import WebSessionModel


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _to_domain(session: WebSessionModel) -> WebSession:
    return WebSession(
        id=session.id,
        session_id=session.session_id,
        guild_id=session.guild_id,
        channel_id=session.channel_id,
        current_track_id=session.current_track_id,
        activated_by=session.activated_by,
        is_active=session.is_active,
        activated_at=session.activated_at,
        ended_at=session.ended_at,
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


class PostgresWebSessionRepository(WebSessionRepository):
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def get_by_session_id(self, *, session_id: UUID) -> WebSession | None:
        async with self._session_factory() as session:
            result = await session.scalar(
                select(WebSessionModel).where(WebSessionModel.session_id == session_id)
            )
            return _to_domain(result) if result else None

    async def get_for_channel(self, *, guild_id: int, channel_id: int) -> WebSession | None:
        async with self._session_factory() as session:
            result = await session.scalar(
                select(WebSessionModel)
                .where(
                    WebSessionModel.guild_id == guild_id,
                    WebSessionModel.channel_id == channel_id,
                )
                .order_by(WebSessionModel.created_at.desc())
                .limit(1)
            )
            return _to_domain(result) if result else None

    async def activate(self, data: WebSessionCreate) -> WebSession:
        async with self._session_factory() as session:
            existing = await session.scalar(
                select(WebSessionModel)
                .where(
                    WebSessionModel.guild_id == data.guild_id,
                    WebSessionModel.channel_id == data.channel_id,
                )
                .order_by(WebSessionModel.created_at.desc())
                .limit(1)
            )
            now = _now()
            if existing is None:
                created = WebSessionModel(
                    guild_id=data.guild_id,
                    channel_id=data.channel_id,
                    current_track_id=data.current_track_id,
                    activated_by=data.activated_by,
                    is_active=True,
                    activated_at=now,
                    ended_at=None,
                    created_at=now,
                    updated_at=now,
                )
                session.add(created)
                await session.commit()
                await session.refresh(created)
                return _to_domain(created)

            existing.current_track_id = data.current_track_id
            existing.activated_by = data.activated_by
            existing.is_active = True
            existing.activated_at = now
            existing.ended_at = None
            existing.updated_at = now
            await session.commit()
            await session.refresh(existing)
            return _to_domain(existing)

    async def set_current_track(self, *, session_id: UUID, track_id: UUID | None) -> WebSession:
        async with self._session_factory() as session:
            existing = await session.scalar(
                select(WebSessionModel).where(WebSessionModel.session_id == session_id)
            )
            if existing is None:
                raise KeyError(f"Web session not found: {session_id}")
            existing.current_track_id = track_id
            existing.updated_at = _now()
            await session.commit()
            await session.refresh(existing)
            return _to_domain(existing)

    async def deactivate(self, *, session_id: UUID) -> WebSession:
        async with self._session_factory() as session:
            existing = await session.scalar(
                select(WebSessionModel).where(WebSessionModel.session_id == session_id)
            )
            if existing is None:
                raise KeyError(f"Web session not found: {session_id}")
            now = _now()
            existing.is_active = False
            existing.ended_at = now
            existing.updated_at = now
            await session.commit()
            await session.refresh(existing)
            return _to_domain(existing)
