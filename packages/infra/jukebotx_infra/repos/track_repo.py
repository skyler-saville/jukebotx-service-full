from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from jukebotx_core.ports.repositories import Track, TrackRepository, TrackUpsert
from jukebotx_infra.db.models import TrackModel


def _now() -> datetime:
    """Return the current UTC time."""
    return datetime.now(timezone.utc)


def _to_domain(track: TrackModel) -> Track:
    """Convert a TrackModel to a Track domain object."""
    return Track(
        id=track.id,
        suno_url=track.suno_url,
        title=track.title,
        artist_display=track.artist_display,
        artist_username=track.artist_username,
        lyrics=track.lyrics,
        image_url=track.image_url,
        video_url=track.video_url,
        mp3_url=track.mp3_url,
        opus_url=track.opus_url,
        opus_path=track.opus_path,
        opus_status=track.opus_status,
        opus_transcoded_at=track.opus_transcoded_at,
        created_at=track.created_at,
        updated_at=track.updated_at,
    )


class PostgresTrackRepository(TrackRepository):
    """Postgres-backed repository for tracks."""

    def __init__(self, session_factory: async_sessionmaker) -> None:
        """Initialize the repository with an async session factory."""
        self._session_factory = session_factory

    async def get_by_suno_url(self, suno_url: str) -> Track | None:
        """Fetch a track by its Suno URL."""
        async with self._session_factory() as session:
            result = await session.scalar(select(TrackModel).where(TrackModel.suno_url == suno_url))
            return _to_domain(result) if result else None

    async def upsert(self, data: TrackUpsert) -> Track:
        """Insert or update a track record based on its Suno URL."""
        async with self._session_factory() as session:
            existing = await session.scalar(select(TrackModel).where(TrackModel.suno_url == data.suno_url))
            now = _now()

            if existing:
                existing.title = data.title or existing.title
                existing.artist_display = data.artist_display or existing.artist_display
                existing.artist_username = data.artist_username or existing.artist_username
                existing.lyrics = data.lyrics or existing.lyrics
                existing.image_url = data.image_url or existing.image_url
                existing.video_url = data.video_url or existing.video_url
                existing.mp3_url = data.mp3_url or existing.mp3_url
                existing.opus_url = data.opus_url or existing.opus_url
                existing.opus_path = data.opus_path or existing.opus_path
                existing.opus_status = data.opus_status or existing.opus_status
                existing.opus_transcoded_at = data.opus_transcoded_at or existing.opus_transcoded_at
                existing.updated_at = now
                await session.commit()
                await session.refresh(existing)
                return _to_domain(existing)

            created = TrackModel(
                suno_url=data.suno_url,
                title=data.title,
                artist_display=data.artist_display,
                artist_username=data.artist_username,
                lyrics=data.lyrics,
                image_url=data.image_url,
                video_url=data.video_url,
                mp3_url=data.mp3_url,
                opus_url=data.opus_url,
                opus_path=data.opus_path,
                opus_status=data.opus_status,
                opus_transcoded_at=data.opus_transcoded_at,
                created_at=now,
                updated_at=now,
            )
            session.add(created)
            await session.commit()
            await session.refresh(created)
            return _to_domain(created)

    async def update_opus_metadata(
        self,
        *,
        track_id: UUID,
        opus_url: str | None,
        opus_path: str | None,
        opus_status: str | None,
        opus_transcoded_at: datetime | None,
    ) -> Track:
        async with self._session_factory() as session:
            result = await session.get(TrackModel, track_id)
            if result is None:
                raise KeyError(f"Track not found: {track_id}")
            result.opus_url = opus_url
            result.opus_path = opus_path
            result.opus_status = opus_status
            result.opus_transcoded_at = opus_transcoded_at
            result.updated_at = _now()
            await session.commit()
            await session.refresh(result)
            return _to_domain(result)

    async def get_by_id(self, track_id: UUID) -> Track:
        """Fetch a track by its UUID."""
        async with self._session_factory() as session:
            result = await session.get(TrackModel, track_id)
            if result is None:
                raise KeyError(f"Track not found: {track_id}")
            return _to_domain(result)
