from __future__ import annotations

from dataclasses import dataclass


class AudioRelayError(RuntimeError):
    """Raised when an audio relay cannot prepare or release a stream."""


@dataclass(frozen=True)
class AudioRelayStream:
    """A live stream prepared by an external audio relay."""

    stream_id: str
    stream_url: str


class AudioRelayClient:
    """Port used by playback orchestration to manage live relay streams."""

    async def start_stream(
        self,
        *,
        source_url: str,
        consumer_id: str,
    ) -> AudioRelayStream:
        raise NotImplementedError

    async def stop_stream(self, stream_id: str) -> None:
        raise NotImplementedError
