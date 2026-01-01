from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse
from uuid import UUID

from jukebotx_core.ports.gif_generation_queue import (
    GifCleanupItem,
    GifGenerationQueue,
    GifGenerationRequest,
)
from jukebotx_core.ports.repositories import TrackRepository


_LOG = logging.getLogger(__name__)


class GifGenerator(Protocol):
    async def generate(self, *, track_id: UUID, video_url: str) -> str | None:
        raise NotImplementedError


@dataclass
class NoopGifGenerator:
    async def generate(self, *, track_id: UUID, video_url: str) -> str | None:
        _LOG.info("Skipping GIF generation for track %s (no generator configured).", track_id)
        return None


class InProcessGifGenerationQueue(GifGenerationQueue):
    def __init__(
        self,
        *,
        track_repo: TrackRepository,
        generator: GifGenerator | None = None,
    ) -> None:
        self._track_repo = track_repo
        self._generator = generator or NoopGifGenerator()
        self._queue: asyncio.Queue[GifGenerationRequest] = asyncio.Queue()
        self._worker_task: asyncio.Task[None] | None = None

    async def enqueue(self, request: GifGenerationRequest) -> None:
        self._ensure_worker()
        await self._queue.put(request)

    async def delete_generated_gifs(self, items: list[GifCleanupItem]) -> None:
        for item in items:
            await self._delete_gif(item)

    def _ensure_worker(self) -> None:
        if self._worker_task is None or self._worker_task.done():
            loop = asyncio.get_running_loop()
            self._worker_task = loop.create_task(self._run_worker())

    async def _run_worker(self) -> None:
        while True:
            request = await self._queue.get()
            try:
                gif_url = await self._generator.generate(
                    track_id=request.track_id,
                    video_url=request.video_url,
                )
                if gif_url:
                    await self._track_repo.update_gif_url(track_id=request.track_id, gif_url=gif_url)
            except Exception as exc:
                _LOG.warning("Failed to generate GIF for track %s: %s", request.track_id, exc)
            finally:
                self._queue.task_done()

    async def _delete_gif(self, item: GifCleanupItem) -> None:
        path = self._url_to_path(item.gif_url)
        if path is None:
            return
        try:
            Path(path).unlink(missing_ok=True)
        except OSError as exc:
            _LOG.warning("Failed to delete GIF %s: %s", item.gif_url, exc)
            return
        await self._track_repo.update_gif_url(track_id=item.track_id, gif_url=None)

    def _url_to_path(self, gif_url: str) -> str | None:
        parsed = urlparse(gif_url)
        if parsed.scheme in ("", "file"):
            return os.path.abspath(parsed.path)
        return None
