# ruff: noqa: E402
from collections.abc import AsyncIterator
import asyncio
from pathlib import Path
import shutil
import sys
from urllib.parse import urlparse

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.extend(
    [
        str(ROOT / "apps" / "relay"),
    ]
)

from jukebotx_relay.engine import RelayEngine, RelaySourceError, SyntheticToneEngine
from jukebotx_relay.browser_engine import SunoBrowserAudioEngine
from jukebotx_relay.main import create_app
from jukebotx_relay.settings import RelaySettings


class FakeRelayEngine(RelayEngine):
    name = "fake"
    content_type = "audio/ogg"

    def supports(self, source_url: str) -> bool:
        return source_url.startswith("https://example.test/")

    def validate_source(self, source_url: str) -> None:
        if source_url.endswith("/invalid"):
            raise RelaySourceError("Invalid fake source")

    async def stream(
        self,
        source_url: str,
        *,
        stop_event: asyncio.Event,
    ) -> AsyncIterator[bytes]:
        del source_url
        if not stop_event.is_set():
            yield b"OggS-fake-audio"


def _build_app():
    settings = RelaySettings(
        RELAY_PUBLIC_BASE_URL="http://relay:8090",
        AUDIO_RELAY_TOKEN="test-token",
    )
    return create_app(settings=settings, engines=[FakeRelayEngine()])


@pytest.mark.asyncio
async def test_relay_stream_can_be_reopened_for_lavalink_probe() -> None:
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": "Bearer test-token"}

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        unauthorized = await client.post(
            "/v1/streams",
            json={
                "source_url": "https://example.test/song/123",
                "consumer_id": "discord-guild:42",
            },
        )
        assert unauthorized.status_code == 401

        created = await client.post(
            "/v1/streams",
            headers=headers,
            json={
                "source_url": "https://example.test/song/123",
                "consumer_id": "discord-guild:42",
            },
        )
        assert created.status_code == 201
        stream_path = urlparse(created.json()["stream_url"]).path

        streamed = await client.get(stream_path)
        assert streamed.status_code == 200
        assert streamed.headers["content-type"].startswith("audio/ogg")
        assert streamed.content == b"OggS-fake-audio"

        replayed = await client.get(stream_path)
        assert replayed.status_code == 200
        assert replayed.content == b"OggS-fake-audio"

        stopped = await client.delete(
            f"/v1/streams/{created.json()['id']}",
            headers=headers,
        )
        assert stopped.status_code == 204


@pytest.mark.asyncio
async def test_new_stream_for_consumer_invalidates_previous_stream() -> None:
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": "Bearer test-token"}
    payload = {
        "source_url": "https://example.test/song/123",
        "consumer_id": "discord-guild:42",
    }

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.post("/v1/streams", headers=headers, json=payload)
        second = await client.post("/v1/streams", headers=headers, json=payload)

        first_path = urlparse(first.json()["stream_url"]).path
        second_path = urlparse(second.json()["stream_url"]).path
        assert (await client.get(first_path)).status_code == 410
        assert (await client.get(second_path)).status_code == 200


@pytest.mark.asyncio
async def test_relay_rejects_unsupported_source() -> None:
    app = _build_app()
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/streams",
            headers={"Authorization": "Bearer test-token"},
            json={
                "source_url": "https://unsupported.test/song/123",
                "consumer_id": "discord-guild:42",
            },
        )

    assert response.status_code == 422
    assert (
        response.json()["detail"] == "No relay engine is configured for this source URL"
    )


@pytest.mark.parametrize(
    ("source_url", "message"),
    [
        ("synthetic://tone?frequency=10", "frequency"),
        ("synthetic://tone?duration=0", "duration"),
        ("synthetic://tone?frequency=nope", "numbers"),
    ],
)
def test_synthetic_engine_validates_parameters(source_url: str, message: str) -> None:
    engine = SyntheticToneEngine()

    with pytest.raises(RelaySourceError, match=message):
        engine.validate_source(source_url)


@pytest.mark.asyncio
@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg is not installed")
async def test_synthetic_engine_emits_ogg_audio() -> None:
    engine = SyntheticToneEngine()
    chunks = [
        chunk
        async for chunk in engine.stream(
            "synthetic://tone?frequency=440&duration=0.1",
            stop_event=asyncio.Event(),
        )
    ]

    audio = b"".join(chunks)
    assert len(audio) > 64
    assert audio.startswith(b"OggS")


@pytest.mark.parametrize(
    "source_url",
    [
        "https://suno.com/song/b64a51c8-e618-4b21-a057-7867e6a98e13",
        "https://suno.com/s/EyLlqP2PRu4s2ph6",
        "https://www.suno.com/song/b64a51c8-e618-4b21-a057-7867e6a98e13",
    ],
)
def test_suno_browser_engine_accepts_song_and_share_urls(source_url: str) -> None:
    engine = SunoBrowserAudioEngine(
        ffmpeg_path="ffmpeg",
        chromium_path="chromium",
        profile_dir=Path("/tmp/test-profile"),
        pulse_server=object(),  # type: ignore[arg-type]
    )

    assert engine.supports(source_url)
    engine.validate_source(source_url)


@pytest.mark.parametrize(
    "source_url",
    [
        "http://suno.com/song/example",
        "https://evil.example/song/example",
        "https://suno.com/create",
    ],
)
def test_suno_browser_engine_rejects_non_song_urls(source_url: str) -> None:
    engine = SunoBrowserAudioEngine(
        ffmpeg_path="ffmpeg",
        chromium_path="chromium",
        profile_dir=Path("/tmp/test-profile"),
        pulse_server=object(),  # type: ignore[arg-type]
    )

    with pytest.raises(RelaySourceError):
        engine.validate_source(source_url)


def test_browser_engine_is_registered_only_when_enabled() -> None:
    disabled = create_app(
        settings=RelaySettings(
            RELAY_ENABLE_BROWSER_INPUTS=False,
            RELAY_ENABLE_SYNTHETIC_INPUTS=False,
        )
    )
    enabled = create_app(
        settings=RelaySettings(
            RELAY_ENABLE_BROWSER_INPUTS=True,
            RELAY_ENABLE_SYNTHETIC_INPUTS=False,
        )
    )

    assert disabled.state.relay_manager.engine_names == ()
    assert enabled.state.relay_manager.engine_names == ("suno-browser",)
