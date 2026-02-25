from __future__ import annotations

from uuid import uuid4

import pytest

from jukebotx_core.use_cases.enqueue_track import EnqueueTrack, EnqueueTrackInput
from jukebotx_core.use_cases.list_queue import ListQueue
from jukebotx_core.use_cases.set_guild_config import SetGuildConfig, SetGuildConfigInput
from jukebotx_core.use_cases.skip_track import SkipTrack
from jukebotx_infra.repos.guild_config_repo import InMemoryGuildConfigRepository
from jukebotx_infra.repos.memory import InMemoryQueueRepository


@pytest.mark.asyncio
async def test_enqueue_and_list_queue_use_cases() -> None:
    queue_repo = InMemoryQueueRepository()
    enqueue = EnqueueTrack(queue_repo=queue_repo)
    list_queue = ListQueue(queue_repo=queue_repo)

    track_id = uuid4()
    result = await enqueue.execute(
        EnqueueTrackInput(guild_id=123, track_id=track_id, requested_by=456)
    )

    listed = await list_queue.execute(guild_id=123)

    assert result.item.track_id == track_id
    assert len(listed.items) == 1
    assert listed.items[0].id == result.item.id


@pytest.mark.asyncio
async def test_skip_track_use_case_marks_item_skipped() -> None:
    queue_repo = InMemoryQueueRepository()
    enqueue = EnqueueTrack(queue_repo=queue_repo)
    skip = SkipTrack(queue_repo=queue_repo)

    queued = await enqueue.execute(
        EnqueueTrackInput(guild_id=123, track_id=uuid4(), requested_by=456)
    )

    await skip.execute(guild_id=123, queue_item_id=queued.item.id)

    listed = await queue_repo.list(guild_id=123)
    assert listed == []


@pytest.mark.asyncio
async def test_set_guild_config_upserts_values() -> None:
    repo = InMemoryGuildConfigRepository()
    use_case = SetGuildConfig(guild_config_repo=repo)

    result = await use_case.execute(
        SetGuildConfigInput(guild_id=42, submission_cooldown_seconds=120, autoplay_enabled=True)
    )

    assert result.config.guild_id == 42
    assert result.config.submission_cooldown_seconds == 120
    assert result.config.autoplay_enabled is True
