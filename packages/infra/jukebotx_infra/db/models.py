from __future__ import annotations

from datetime import datetime
from uuid import UUID as PyUUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func, BigInteger
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class for SQLAlchemy declarative models."""


class TrackModel(Base):
    """Database model for a Suno track."""

    __tablename__ = "tracks"

    id: Mapped[PyUUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    suno_url: Mapped[str] = mapped_column(String(512), unique=True, index=True, nullable=False)
    title: Mapped[str | None] = mapped_column(String(255))
    artist_display: Mapped[str | None] = mapped_column(String(255))
    artist_username: Mapped[str | None] = mapped_column(String(255))
    lyrics: Mapped[str | None] = mapped_column(Text)
    gif_url: Mapped[str | None] = mapped_column(String(1024))
    image_url: Mapped[str | None] = mapped_column(String(1024))
    video_url: Mapped[str | None] = mapped_column(String(1024))
    mp3_url: Mapped[str | None] = mapped_column(String(1024))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SubmissionModel(Base):
    """Database model for a track submission in a guild."""

    __tablename__ = "submissions"

    id: Mapped[PyUUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    track_id: Mapped[PyUUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tracks.id"), nullable=False)
    guild_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    author_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class QueueItemModel(Base):
    """Database model for a queued track in a guild."""

    __tablename__ = "queue_items"

    id: Mapped[PyUUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    guild_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    track_id: Mapped[PyUUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tracks.id"), nullable=False)

    # Discord user snowflake -> MUST be BigInteger
    requested_by: Mapped[int] = mapped_column(BigInteger, nullable=False)

    status: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
