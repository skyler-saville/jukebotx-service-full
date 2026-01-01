from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class GifGenerationRequest:
    track_id: UUID
    video_url: str


@dataclass(frozen=True)
class GifCleanupItem:
    track_id: UUID
    gif_url: str


class GifGenerationQueue:
    async def enqueue(self, request: GifGenerationRequest) -> None:
        raise NotImplementedError

    async def delete_generated_gifs(self, items: list[GifCleanupItem]) -> None:
        raise NotImplementedError
