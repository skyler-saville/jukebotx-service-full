from __future__ import annotations

from collections.abc import AsyncIterator
import hmac

from fastapi import FastAPI, Header, HTTPException, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from jukebotx_relay.browser_engine import PulseAudioServer, SunoBrowserAudioEngine
from jukebotx_relay.engine import RelayEngine, RelaySourceError, SyntheticToneEngine
from jukebotx_relay.service import (
    RelaySessionManager,
    RelaySessionNotFound,
    RelaySessionUnavailable,
)
from jukebotx_relay.settings import RelaySettings


class CreateStreamRequest(BaseModel):
    source_url: str = Field(min_length=1, max_length=4096)
    consumer_id: str = Field(min_length=1, max_length=256)


class CreateStreamResponse(BaseModel):
    id: str
    stream_url: str


def create_app(
    *,
    settings: RelaySettings | None = None,
    engines: list[RelayEngine] | None = None,
) -> FastAPI:
    resolved_settings = settings or RelaySettings()
    if engines is None:
        engines = []
        if resolved_settings.enable_browser_inputs:
            pulse_server = PulseAudioServer(
                server=resolved_settings.pulse_server,
                runtime_dir=resolved_settings.pulse_runtime_dir,
                pulseaudio_path=resolved_settings.pulseaudio_path,
                pactl_path=resolved_settings.pactl_path,
            )
            engines.append(
                SunoBrowserAudioEngine(
                    ffmpeg_path=resolved_settings.ffmpeg_path,
                    chromium_path=resolved_settings.chromium_path,
                    profile_dir=resolved_settings.browser_profile_dir,
                    pulse_server=pulse_server,
                    navigation_timeout_seconds=(
                        resolved_settings.browser_navigation_timeout_seconds
                    ),
                    playback_timeout_seconds=(
                        resolved_settings.browser_playback_timeout_seconds
                    ),
                )
            )
        if resolved_settings.enable_synthetic_inputs:
            engines.append(
                SyntheticToneEngine(ffmpeg_path=resolved_settings.ffmpeg_path)
            )
    manager = RelaySessionManager(engines=engines)

    app = FastAPI(title="JukeBotx Audio Relay", version="0.1.0")
    app.state.relay_manager = manager

    def require_control_token(authorization: str | None = Header(default=None)) -> None:
        expected = resolved_settings.control_token
        if expected is None:
            return
        prefix = "Bearer "
        provided = (
            authorization[len(prefix) :]
            if authorization and authorization.startswith(prefix)
            else ""
        )
        if not hmac.compare_digest(provided, expected):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

    @app.get("/health")
    async def health() -> dict[str, object]:
        return {
            "status": "ok",
            "engines": list(manager.engine_names),
        }

    @app.post(
        "/v1/streams",
        response_model=CreateStreamResponse,
        status_code=status.HTTP_201_CREATED,
        dependencies=[],
    )
    async def create_stream(
        request: CreateStreamRequest,
        authorization: str | None = Header(default=None),
    ) -> CreateStreamResponse:
        require_control_token(authorization)
        try:
            session = await manager.create(
                source_url=request.source_url,
                consumer_id=request.consumer_id,
            )
        except RelaySourceError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(exc),
            ) from exc

        try:
            await manager.prepare(session)
        except RelaySessionUnavailable as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="The relay source could not begin playback",
            ) from exc

        return CreateStreamResponse(
            id=session.stream_id,
            stream_url=(
                f"{resolved_settings.public_base_url}/v1/streams/"
                f"{session.stream_id}/audio"
            ),
        )

    @app.get("/v1/streams/{stream_id}/audio")
    async def stream_audio(stream_id: str) -> StreamingResponse:
        try:
            session = await manager.claim(stream_id)
        except RelaySessionNotFound as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from exc
        except RelaySessionUnavailable as exc:
            raise HTTPException(status_code=status.HTTP_410_GONE) from exc

        async def chunks() -> AsyncIterator[bytes]:
            async for chunk in manager.stream(stream_id):
                yield chunk

        return StreamingResponse(chunks(), media_type=session.engine.content_type)

    @app.delete("/v1/streams/{stream_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def stop_stream(
        stream_id: str,
        authorization: str | None = Header(default=None),
    ) -> Response:
        require_control_token(authorization)
        if not await manager.stop(stream_id):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return app


app = create_app()


def run() -> None:
    import uvicorn

    settings = RelaySettings()
    uvicorn.run(
        "jukebotx_relay.main:app",
        host=settings.bind_host,
        port=settings.port,
    )


if __name__ == "__main__":
    run()
