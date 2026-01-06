from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID as PyUUID, uuid4

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum as SqlEnum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
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
    image_url: Mapped[str | None] = mapped_column(String(1024))
    video_url: Mapped[str | None] = mapped_column(String(1024))
    mp3_url: Mapped[str | None] = mapped_column(String(1024))
    opus_url: Mapped[str | None] = mapped_column(String(1024))
    opus_path: Mapped[str | None] = mapped_column(String(1024))
    opus_status: Mapped[str | None] = mapped_column(String(32))
    opus_transcoded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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


class JamSessionStatus(Enum):
    ACTIVE = "active"
    ENDED = "ended"


class SessionReactionType(Enum):
    UPVOTE = "upvote"
    DOWNVOTE = "downvote"


class JamSessionModel(Base):
    """Database model for a jam session lifecycle."""

    __tablename__ = "jam_sessions"
    __table_args__ = (
        Index(
            "ix_jam_sessions_active_guild",
            "guild_id",
            unique=True,
            postgresql_where=text("ended_at IS NULL"),
        ),
    )

    id: Mapped[PyUUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    guild_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    channel_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    status: Mapped[JamSessionStatus] = mapped_column(
        SqlEnum(JamSessionStatus, name="jam_session_status"),
        index=True,
        nullable=False,
        default=JamSessionStatus.ACTIVE,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class QueueItemModel(Base):
    """Database model for a queued track in a guild."""

    __tablename__ = "queue_items"

    id: Mapped[PyUUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    guild_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    session_id: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jam_sessions.id"),
        index=True,
    )
    track_id: Mapped[PyUUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tracks.id"), nullable=False)

    # Discord user snowflake -> MUST be BigInteger
    requested_by: Mapped[int] = mapped_column(BigInteger, nullable=False)

    status: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SessionReactionModel(Base):
    """Database model for reactions on session tracks."""

    __tablename__ = "session_reactions"
    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "track_id",
            "user_id",
            "reaction_type",
            name="uq_session_reactions_unique",
        ),
    )

    id: Mapped[PyUUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    session_id: Mapped[PyUUID] = mapped_column(UUID(as_uuid=True), ForeignKey("jam_sessions.id"), index=True)
    track_id: Mapped[PyUUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tracks.id"), index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reaction_type: Mapped[SessionReactionType] = mapped_column(
        SqlEnum(SessionReactionType, name="session_reaction_type"),
        index=True,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OpusJobModel(Base):
    """Database model for Opus transcode jobs."""

    __tablename__ = "opus_jobs"

    id: Mapped[PyUUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    track_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tracks.id"),
        nullable=False,
        unique=True,
    )
    mp3_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    status: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
