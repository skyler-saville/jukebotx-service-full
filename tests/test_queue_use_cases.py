import asyncio
from pathlib import Path
import sys
from uuid import uuid4

from contextlib import asynccontextmanager
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.extend([str(ROOT / "packages" / "core"), str(ROOT / "packages" / "infra")])

if "async_timeout" not in sys.modules:
    mod = ModuleType("async_timeout")

    @asynccontextmanager
    async def timeout(*args, **kwargs):
        yield

    mod.timeout = timeout
    sys.modules["async_timeout"] = mod

from jukebotx_core.use_cases.enqueue_track import EnqueueTrack, EnqueueTrackInput
from jukebotx_core.use_cases.list_queue import ListQueue
from jukebotx_core.use_cases.remove_queue_item import RemoveQueueItem
from jukebotx_core.use_cases.set_guild_config import SetGuildConfig, SetGuildConfigInput
from jukebotx_core.use_cases.skip_track import SkipTrack
from jukebotx_infra.repos.guild_config_repo import InMemoryGuildConfigRepository
from jukebotx_infra.repos.memory import InMemoryQueueRepository


def test_enqueue_and_list_queue_use_cases() -> None:
    queue_repo = InMemoryQueueRepository()
    enqueue = EnqueueTrack(queue_repo=queue_repo)
    list_queue = ListQueue(queue_repo=queue_repo)

    track_id = uuid4()
    result = asyncio.run(enqueue.execute(
        EnqueueTrackInput(guild_id=123, track_id=track_id, requested_by=456)
    ))

    listed = asyncio.run(list_queue.execute(guild_id=123))

    assert result.item.track_id == track_id
    assert len(listed.items) == 1
    assert listed.items[0].id == result.item.id


def test_skip_track_use_case_marks_item_skipped() -> None:
    queue_repo = InMemoryQueueRepository()
    enqueue = EnqueueTrack(queue_repo=queue_repo)
    skip = SkipTrack(queue_repo=queue_repo)

    queued = asyncio.run(enqueue.execute(
        EnqueueTrackInput(guild_id=123, track_id=uuid4(), requested_by=456)
    ))

    asyncio.run(skip.execute(guild_id=123, queue_item_id=queued.item.id))

    listed = asyncio.run(queue_repo.list(guild_id=123))
    assert listed == []


def test_remove_queue_item_use_case_removes_item() -> None:
    queue_repo = InMemoryQueueRepository()
    enqueue = EnqueueTrack(queue_repo=queue_repo)
    remove = RemoveQueueItem(queue_repo=queue_repo)

    queued = asyncio.run(enqueue.execute(
        EnqueueTrackInput(guild_id=123, track_id=uuid4(), requested_by=456)
    ))

    asyncio.run(remove.execute(guild_id=123, queue_item_id=queued.item.id))

    listed = asyncio.run(queue_repo.list(guild_id=123))
    assert listed == []


def test_set_guild_config_upserts_values() -> None:
    repo = InMemoryGuildConfigRepository()
    use_case = SetGuildConfig(guild_config_repo=repo)

    result = asyncio.run(use_case.execute(
        SetGuildConfigInput(guild_id=42, session_open=False, session_track_limit=5, submission_cooldown_seconds=120, autoplay_enabled=True)
    ))

    assert result.config.guild_id == 42
    assert result.config.session_open is False
    assert result.config.session_track_limit == 5
    assert result.config.submission_cooldown_seconds == 120
    assert result.config.autoplay_enabled is True
