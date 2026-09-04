from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from uuid import uuid4

from jukebotx_relay.engine import RelayEngine, RelaySourceError


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
            if session.status is not RelaySessionStatus.READY:
                raise RelaySessionUnavailable(stream_id)
            session.status = RelaySessionStatus.STREAMING
            return session

    async def finish(self, stream_id: str) -> None:
        async with self._lock:
            session = self._sessions.get(stream_id)
            if session is None:
                return
            session.status = RelaySessionStatus.STOPPED
            if self._stream_by_consumer.get(session.consumer_id) == stream_id:
                self._stream_by_consumer.pop(session.consumer_id, None)

    async def stop(self, stream_id: str) -> bool:
        async with self._lock:
            session = self._sessions.get(stream_id)
            if session is None:
                return False
            session.status = RelaySessionStatus.STOPPED
            session.stop_event.set()
            if self._stream_by_consumer.get(session.consumer_id) == stream_id:
                self._stream_by_consumer.pop(session.consumer_id, None)
            return True

    def _find_engine(self, source_url: str) -> RelayEngine:
        for engine in self._engines:
            if engine.supports(source_url):
                return engine
        raise RelaySourceError("No relay engine is configured for this source URL")
