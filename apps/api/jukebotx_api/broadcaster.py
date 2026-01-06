from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator
from uuid import UUID

from jukebotx_core.contracts import EventEnvelope


class SessionEventBroadcaster:
    def __init__(self, *, max_queue_size: int = 100) -> None:
        self._max_queue_size = max_queue_size
        self._subscriptions: dict[UUID, set[asyncio.Queue[EventEnvelope]]] = {}
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def subscribe(self, session_id: UUID) -> AsyncIterator[asyncio.Queue[EventEnvelope]]:
        queue: asyncio.Queue[EventEnvelope] = asyncio.Queue(maxsize=self._max_queue_size)
        async with self._lock:
            self._subscriptions.setdefault(session_id, set()).add(queue)
        try:
            yield queue
        finally:
            async with self._lock:
                queues = self._subscriptions.get(session_id)
                if queues:
                    queues.discard(queue)
                    if not queues:
                        self._subscriptions.pop(session_id, None)

    async def publish(self, session_id: UUID, envelope: EventEnvelope) -> None:
        async with self._lock:
            queues = list(self._subscriptions.get(session_id, set()))
        for queue in queues:
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                queue.put_nowait(envelope)
            except asyncio.QueueFull:
                continue


event_broadcaster = SessionEventBroadcaster()


def get_event_broadcaster() -> SessionEventBroadcaster:
    return event_broadcaster
