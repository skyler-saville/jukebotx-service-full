from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import Enum
import logging
from uuid import uuid4

from jukebotx_relay.engine import RelayEngine, RelaySourceError

logger = logging.getLogger(__name__)


class RelaySessionStatus(str, Enum):
    READY = "ready"
    STREAMING = "streaming"
    STOPPED = "stopped"


class RelaySessionNotFound(LookupError):
    pass


class RelaySessionUnavailable(RuntimeError):
    pass


@dataclass
class RelaySession:
    stream_id: str
    source_url: str
    consumer_id: str
    engine: RelayEngine
    status: RelaySessionStatus = RelaySessionStatus.READY
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)
    chunks: list[bytes] = field(default_factory=list)
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    producer_task: asyncio.Task[None] | None = None
    finished: bool = False
    error: BaseException | None = None


class RelaySessionManager:
    def __init__(self, *, engines: list[RelayEngine]) -> None:
        self._engines = tuple(engines)
        self._sessions: dict[str, RelaySession] = {}
        self._stream_by_consumer: dict[str, str] = {}
        self._lock = asyncio.Lock()

    @property
    def engine_names(self) -> tuple[str, ...]:
        return tuple(engine.name for engine in self._engines)

    async def create(self, *, source_url: str, consumer_id: str) -> RelaySession:
        engine = self._find_engine(source_url)
        engine.validate_source(source_url)

        async with self._lock:
            previous_id = self._stream_by_consumer.get(consumer_id)
            if previous_id is not None:
                previous = self._sessions.get(previous_id)
                if previous is not None:
                    previous.status = RelaySessionStatus.STOPPED
                    previous.stop_event.set()

            stream_id = uuid4().hex
            session = RelaySession(
                stream_id=stream_id,
                source_url=source_url,
                consumer_id=consumer_id,
                engine=engine,
            )
            self._sessions[stream_id] = session
            self._stream_by_consumer[consumer_id] = stream_id
            return session

    async def claim(self, stream_id: str) -> RelaySession:
        async with self._lock:
            session = self._sessions.get(stream_id)
            if session is None:
                raise RelaySessionNotFound(stream_id)
            if session.status is RelaySessionStatus.STOPPED:
                raise RelaySessionUnavailable(stream_id)
            if session.producer_task is None:
                session.status = RelaySessionStatus.STREAMING
                session.producer_task = asyncio.create_task(self._produce(session))
            return session

    async def prepare(self, session: RelaySession) -> None:
        """Start capture and wait until an HTTP consumer can read immediately."""
        await self.claim(session.stream_id)
        async with session.condition:
            await session.condition.wait_for(
                lambda: bool(session.chunks) or session.finished
            )
            if session.error is not None:
                raise RelaySessionUnavailable(session.stream_id) from session.error
            if not session.chunks:
                raise RelaySessionUnavailable(session.stream_id)

    async def stream(self, stream_id: str) -> AsyncIterator[bytes]:
        session = await self.claim(stream_id)
        cursor = 0
        while True:
            async with session.condition:
                await session.condition.wait_for(
                    lambda: cursor < len(session.chunks)
                    or session.finished
                    or session.stop_event.is_set()
                )
                if cursor < len(session.chunks):
                    chunk = session.chunks[cursor]
                    cursor += 1
                else:
                    if session.error is not None:
                        raise session.error
                    return
            yield chunk

    async def _produce(self, session: RelaySession) -> None:
        try:
            async for chunk in session.engine.stream(
                session.source_url,
                stop_event=session.stop_event,
            ):
                async with session.condition:
                    session.chunks.append(chunk)
                    session.condition.notify_all()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            session.error = exc
            logger.exception("Relay producer failed for stream %s", session.stream_id)
        finally:
            async with session.condition:
                session.finished = True
                session.condition.notify_all()

    async def finish(self, stream_id: str) -> None:
        async with self._lock:
            session = self._sessions.get(stream_id)
            if session is None:
                return
            session.status = RelaySessionStatus.STOPPED
            session.stop_event.set()
            if self._stream_by_consumer.get(session.consumer_id) == stream_id:
                self._stream_by_consumer.pop(session.consumer_id, None)
        async with session.condition:
            session.condition.notify_all()

    async def stop(self, stream_id: str) -> bool:
        async with self._lock:
            session = self._sessions.get(stream_id)
            if session is None:
                return False
            session.status = RelaySessionStatus.STOPPED
            session.stop_event.set()
            if self._stream_by_consumer.get(session.consumer_id) == stream_id:
                self._stream_by_consumer.pop(session.consumer_id, None)
        async with session.condition:
            session.condition.notify_all()
        return True

    def _find_engine(self, source_url: str) -> RelayEngine:
        for engine in self._engines:
            if engine.supports(source_url):
                return engine
        raise RelaySourceError("No relay engine is configured for this source URL")
