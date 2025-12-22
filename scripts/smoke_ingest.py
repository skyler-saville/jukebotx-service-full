from __future__ import annotations

import asyncio

from jukebotx_core.use_cases.ingest_suno_links import IngestSunoLink, IngestSunoLinkInput
from jukebotx_infra.repos.memory import (
    InMemoryQueueRepository,
    InMemorySubmissionRepository,
    InMemoryTrackRepository,
)
from jukebotx_infra.suno.client import HttpxSunoClient


async def main() -> None:
    suno = HttpxSunoClient()
    tracks = InMemoryTrackRepository()
    submissions = InMemorySubmissionRepository()
    queue = InMemoryQueueRepository()

    ingest = IngestSunoLink(
        suno_client=suno,
        track_repo=tracks,
        submission_repo=submissions,
        queue_repo=queue,
    )

    url = "https://suno.com/..."  # replace
    guild_id = 111
    channel_id = 222

    r1 = await ingest.execute(
        IngestSunoLinkInput(
            guild_id=guild_id,
            channel_id=channel_id,
            message_id=1,
            author_id=999,
            suno_url=url,
            auto_enqueue=True,
        )
    )
    print("First ingest:", r1)

    r2 = await ingest.execute(
        IngestSunoLinkInput(
            guild_id=guild_id,
            channel_id=channel_id,
            message_id=2,
            author_id=888,
            suno_url=url,
            auto_enqueue=True,
        )
    )
    print("Second ingest (same guild, should mark duplicate):", r2)

    r3 = await ingest.execute(
        IngestSunoLinkInput(
            guild_id=777,  # different guild
            channel_id=333,
            message_id=3,
            author_id=7777,
            suno_url=url,
            auto_enqueue=True,
        )
    )
    print("Third ingest (different guild, should not be dup-in-guild):", r3)


if __name__ == "__main__":
    asyncio.run(main())
