from __future__ import annotations

import os

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from jukebotx_infra.db.models import Base

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://jukebotx:jukebotx@localhost:5432/jukebotx",
)

engine: AsyncEngine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
"""Async SQLAlchemy engine configured for Postgres."""

async_session_factory = async_sessionmaker(engine, expire_on_commit=False)
"""Session factory for creating async DB sessions."""


async def init_db() -> None:
    """Create database tables based on SQLAlchemy metadata."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
