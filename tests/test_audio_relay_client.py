# ruff: noqa: E402
from pathlib import Path
import sys

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.extend(
    [
        str(ROOT / "packages" / "core"),
        str(ROOT / "packages" / "infra"),
    ]
)

from jukebotx_core.ports.audio_relay import AudioRelayError
from jukebotx_infra.audio_relay import HttpAudioRelayClient


@pytest.mark.asyncio
async def test_http_audio_relay_client_starts_and_stops_stream() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(
                201,
                json={
                    "id": "session/one",
                    "stream_url": "http://relay:8090/v1/streams/session/audio",
                },
            )
        return httpx.Response(204)

    client = HttpAudioRelayClient(
        base_url="http://relay:8090/",
        token="test-token",
        transport=httpx.MockTransport(handler),
    )

    stream = await client.start_stream(
        source_url="https://example.test/song/123",
        consumer_id="discord-guild:42",
    )
    await client.stop_stream(stream.stream_id)

    assert stream.stream_id == "session/one"
    assert stream.stream_url == "http://relay:8090/v1/streams/session/audio"
    assert requests[0].headers["authorization"] == "Bearer test-token"
    assert requests[0].method == "POST"
    assert requests[0].url == "http://relay:8090/v1/streams"
    assert requests[0].content == (
        b'{"source_url":"https://example.test/song/123",'
        b'"consumer_id":"discord-guild:42"}'
    )
    assert requests[1].method == "DELETE"
    assert requests[1].url == "http://relay:8090/v1/streams/session%2Fone"


@pytest.mark.asyncio
async def test_http_audio_relay_client_rejects_invalid_stream_url() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            201,
            json={"id": "relay-1", "stream_url": "file:///tmp/audio.opus"},
        )

    client = HttpAudioRelayClient(
        base_url="http://relay:8090",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(AudioRelayError, match="valid stream URL"):
        await client.start_stream(
            source_url="https://example.test/song/123",
            consumer_id="discord-guild:42",
        )


@pytest.mark.asyncio
async def test_http_audio_relay_client_wraps_start_failure() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="relay unavailable")

    client = HttpAudioRelayClient(
        base_url="http://relay:8090",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(AudioRelayError, match="failed to prepare"):
        await client.start_stream(
            source_url="https://example.test/song/123",
            consumer_id="discord-guild:42",
        )


@pytest.mark.asyncio
async def test_http_audio_relay_client_treats_missing_release_as_complete() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    client = HttpAudioRelayClient(
        base_url="http://relay:8090",
        transport=httpx.MockTransport(handler),
    )

    await client.stop_stream("already-gone")
