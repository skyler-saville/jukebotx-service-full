from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from jukebotx_core.ports.repositories import (
    Submission,
    SubmissionCreate,
    SubmissionRepository,
    SubmissionTrackInfo,
)
from jukebotx_infra.db.models import SubmissionModel
from jukebotx_infra.db.models import TrackModel


def _now() -> datetime:
    """Return the current UTC time."""
    return datetime.now(timezone.utc)


def _to_domain(submission: SubmissionModel) -> Submission:
    """Convert a SubmissionModel to a Submission domain object."""
    return Submission(
        id=submission.id,
        track_id=submission.track_id,
        guild_id=submission.guild_id,
        channel_id=submission.channel_id,
        message_id=submission.message_id,
        author_id=submission.author_id,
        submitted_at=submission.submitted_at,
    )


class PostgresSubmissionRepository(SubmissionRepository):
    """Postgres-backed repository for submissions."""

    def __init__(self, session_factory: async_sessionmaker) -> None:
        """Initialize the repository with an async session factory."""
        self._session_factory = session_factory

    async def get_first_submission_for_track_in_guild(
        self,
        *,
        guild_id: int,
        track_id: UUID,
    ) -> Submission | None:
        """Return the earliest submission for a track within a guild."""
        async with self._session_factory() as session:
            result = await session.scalar(
                select(SubmissionModel)
                .where(SubmissionModel.guild_id == guild_id, SubmissionModel.track_id == track_id)
                .order_by(SubmissionModel.submitted_at.asc())
                .limit(1)
            )
            return _to_domain(result) if result else None

    async def create(self, data: SubmissionCreate) -> Submission:
        """Create a new submission record."""
        async with self._session_factory() as session:
            created = SubmissionModel(
                track_id=data.track_id,
                guild_id=data.guild_id,
                channel_id=data.channel_id,
                message_id=data.message_id,
                author_id=data.author_id,
                submitted_at=_now(),
            )
            session.add(created)
            await session.commit()
            await session.refresh(created)
            return _to_domain(created)

    async def list_tracks_for_channel(
        self,
        *,
        guild_id: int,
        channel_id: int,
    ) -> list[SubmissionTrackInfo]:
        """Fetch track info for submissions in a guild/channel."""
        async with self._session_factory() as session:
            rows = await session.execute(
                select(SubmissionModel, TrackModel)
                .join(TrackModel, SubmissionModel.track_id == TrackModel.id)
                .where(
                    SubmissionModel.guild_id == guild_id,
                    SubmissionModel.channel_id == channel_id,
                )
                .order_by(SubmissionModel.submitted_at.asc())
            )
            return [
                SubmissionTrackInfo(
                    artist_display=track.artist_display,
                    title=track.title,
                    suno_url=track.suno_url,
                    mp3_url=track.mp3_url,
                )
                for _, track in rows.all()
            ]

    async def clear_for_channel(self, *, guild_id: int, channel_id: int) -> int:
        """Remove all submissions for a guild/channel."""
        async with self._session_factory() as session:
            result = await session.execute(
                delete(SubmissionModel).where(
                    SubmissionModel.guild_id == guild_id,
                    SubmissionModel.channel_id == channel_id,
                )
            )
            await session.commit()
            return result.rowcount or 0
