from __future__ import annotations

from urllib.parse import quote, urlparse

import httpx

from jukebotx_core.ports.audio_relay import (
    AudioRelayClient,
    AudioRelayError,
    AudioRelayStream,
)


class HttpAudioRelayClient(AudioRelayClient):
    """HTTP adapter for a browser-audio relay sidecar."""

    def __init__(
        self,
        *,
        base_url: str,
        token: str | None = None,
        timeout_seconds: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        normalized_base_url = base_url.strip().rstrip("/")
        if not normalized_base_url:
            raise ValueError("Audio relay base URL cannot be empty")
        if timeout_seconds <= 0:
            raise ValueError("Audio relay timeout must be greater than zero")

        self._base_url = normalized_base_url
        self._timeout = httpx.Timeout(timeout_seconds)
        self._transport = transport
        self._headers = {"Accept": "application/json"}
        if token:
            self._headers["Authorization"] = f"Bearer {token}"

    async def start_stream(
        self,
        *,
        source_url: str,
        consumer_id: str,
    ) -> AudioRelayStream:
        try:
            async with self._build_client() as client:
                response = await client.post(
                    f"{self._base_url}/v1/streams",
                    json={
                        "source_url": source_url,
                        "consumer_id": consumer_id,
                    },
                )
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise AudioRelayError("Audio relay failed to prepare a stream") from exc

        if not isinstance(payload, dict):
            raise AudioRelayError("Audio relay returned an invalid response")

        stream_id = payload.get("id")
        stream_url = payload.get("stream_url")
        if not isinstance(stream_id, str) or not stream_id.strip():
            raise AudioRelayError("Audio relay response is missing a stream id")
        if not isinstance(stream_url, str) or not self._is_http_url(stream_url):
            raise AudioRelayError("Audio relay response is missing a valid stream URL")

        return AudioRelayStream(
            stream_id=stream_id.strip(),
            stream_url=stream_url.strip(),
        )

    async def stop_stream(self, stream_id: str) -> None:
        encoded_stream_id = quote(stream_id, safe="")
        try:
            async with self._build_client() as client:
                response = await client.delete(
                    f"{self._base_url}/v1/streams/{encoded_stream_id}"
                )
                if response.status_code not in {404, 410}:
                    response.raise_for_status()
        except httpx.HTTPError as exc:
            raise AudioRelayError("Audio relay failed to release a stream") from exc

    def _build_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=self._timeout,
            headers=self._headers,
            transport=self._transport,
        )

    @staticmethod
    def _is_http_url(value: str) -> bool:
        parsed = urlparse(value.strip())
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
