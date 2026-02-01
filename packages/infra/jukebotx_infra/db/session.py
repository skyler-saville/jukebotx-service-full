from __future__ import annotations

import asyncio
import logging
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

logger = logging.getLogger(__name__)


async def init_db() -> None:
    """Create database tables based on SQLAlchemy metadata."""
    max_attempts = int(os.getenv("DB_INIT_MAX_ATTEMPTS", "10"))
    delay_seconds = float(os.getenv("DB_INIT_RETRY_DELAY_SECONDS", "2"))
    attempt = 1
    while True:
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            return
        except Exception as exc:
            if attempt >= max_attempts:
                raise
            logger.warning(
                "DB init failed (attempt %s/%s): %s. Retrying in %.1fs",
                attempt,
                max_attempts,
                exc,
                delay_seconds,
            )
            await asyncio.sleep(delay_seconds)
            attempt += 1
