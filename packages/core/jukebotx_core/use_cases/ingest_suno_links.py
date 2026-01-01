# packages/core/jukebotx_core/use_cases/ingest_suno_links.py
from __future__ import annotations

from dataclasses import dataclass

from jukebotx_core.ports.repositories import (
    QueueItemCreate,
    QueueRepository,
    SubmissionCreate,
    SubmissionRepository,
    TrackRepository,
    TrackUpsert,
)
from jukebotx_core.ports.suno_client import SunoClient


@dataclass(frozen=True)
class IngestSunoLinkInput:
    guild_id: int
    channel_id: int
    message_id: int
    author_id: int
    suno_url: str
    auto_enqueue: bool = False


@dataclass(frozen=True)
class IngestSunoLinkResult:
    is_duplicate_in_guild: bool
    suno_url: str
    track_title: str | None
    artist_display: str | None
    mp3_url: str | None
    media_url: str | None
    queued: bool


class IngestSunoLink:
    """
    Core use-case: process a posted Suno URL.

    Old behavior mapped:
    - Detect duplicates -> previously global; now we consider "duplicate within guild"
    - Scrape metadata -> via SunoClient port (httpx in infra)
    - Store data -> via repositories
    - Optional: enqueue -> per guild

    Multi-guild correctness:
    - Tracks are global by URL
    - Submissions are per guild/channel/message
    - Queue is per guild
    """

    def __init__(
        self,
        *,
        suno_client: SunoClient,
        track_repo: TrackRepository,
        submission_repo: SubmissionRepository,
        queue_repo: QueueRepository,
    ) -> None:
        self._suno_client = suno_client
        self._track_repo = track_repo
        self._submission_repo = submission_repo
        self._queue_repo = queue_repo

    async def execute(self, data: IngestSunoLinkInput) -> IngestSunoLinkResult:
        fetched = await self._suno_client.fetch_track(data.suno_url)

        track = await self._track_repo.upsert(
            TrackUpsert(
                suno_url=fetched.suno_url,
                title=fetched.title,
                artist_display=fetched.artist_display,
                artist_username=fetched.artist_username,
                lyrics=fetched.lyrics,
                image_url=fetched.image_url,
                video_url=fetched.video_url,
                mp3_url=fetched.mp3_url,
            )
        )

        # Guild-local duplicate logic (like your old "already shared" behavior)
        prior = await self._submission_repo.get_first_submission_for_track_in_guild(
            guild_id=data.guild_id,
            track_id=track.id,
        )
        is_dup = prior is not None

        # Always create a submission record (you may want this even if duplicate),
        # but you can choose to skip creating if you want "hard dedupe".
        await self._submission_repo.create(
            SubmissionCreate(
                track_id=track.id,
                guild_id=data.guild_id,
                channel_id=data.channel_id,
                message_id=data.message_id,
                author_id=data.author_id,
            )
        )

        queued = False
        if data.auto_enqueue and (not is_dup):
            await self._queue_repo.enqueue(
                QueueItemCreate(
                    guild_id=data.guild_id,
                    track_id=track.id,
                    requested_by=data.author_id,
                )
            )
            queued = True

        return IngestSunoLinkResult(
            is_duplicate_in_guild=is_dup,
            suno_url=track.suno_url,
            track_title=track.title,
            artist_display=track.artist_display,
            mp3_url=track.mp3_url,
            media_url=track.image_url or track.video_url,
            queued=queued,
        )
