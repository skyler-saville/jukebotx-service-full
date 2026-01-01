import asyncio
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from jukebotx_api.auth import (
    SessionData,
    build_login_redirect,
    build_session_cookie,
    build_state_token,
    clear_session,
    get_session_cookie,
    ensure_oauth_configured,
    exchange_code_for_token,
    fetch_user,
    fetch_user_guilds,
    parse_session_cookie,
    require_session,
    validate_state_token,
)
from jukebotx_api.schemas import (
    NextQueueItemResponse,
    QueueItemSummary,
    QueuePreviewResponse,
    SessionTrackResponse,
    TrackSummary,
)
from jukebotx_api.settings import ApiSettings, load_api_settings
from jukebotx_core.ports.repositories import Track
from jukebotx_core.use_cases.get_queue_preview import GetQueuePreview
from jukebotx_infra.db import async_session_factory
from jukebotx_infra.repos.queue_repo import PostgresQueueRepository
from jukebotx_infra.repos.submission_repo import PostgresSubmissionRepository
from jukebotx_infra.repos.track_repo import PostgresTrackRepository

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="JukeBotx API")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def get_queue_repo() -> PostgresQueueRepository:
    return PostgresQueueRepository(async_session_factory)


def get_track_repo() -> PostgresTrackRepository:
    return PostgresTrackRepository(async_session_factory)


def get_submission_repo() -> PostgresSubmissionRepository:
    return PostgresSubmissionRepository(async_session_factory)


def ensure_guild_access(session: SessionData, guild_id: int) -> None:
    if str(guild_id) not in session.guild_ids:
        raise HTTPException(status_code=403, detail="Forbidden for this guild.")


async def require_track(track_repo: PostgresTrackRepository, track_id: UUID) -> Track:
    try:
        return await track_repo.get_by_id(track_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
async def index(request: Request, settings: ApiSettings = Depends(load_api_settings)):
    session = None
    token = get_session_cookie(request)
    if token and settings.session_secret:
        session = parse_session_cookie(token, settings.session_secret)
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "session": session,
            "guild_ids": session.guild_ids if session else [],
        },
    )


@app.get("/auth/discord/login")
async def discord_login(settings: ApiSettings = Depends(load_api_settings)) -> RedirectResponse:
    ensure_oauth_configured(settings)
    state_token = build_state_token(settings.session_secret)
    return build_login_redirect(settings, state_token)


@app.get("/auth/discord/callback")
async def discord_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    settings: ApiSettings = Depends(load_api_settings),
) -> RedirectResponse:
    ensure_oauth_configured(settings)
    if code is None or state is None:
        raise HTTPException(status_code=400, detail="Missing OAuth code or state.")
    if not validate_state_token(state, settings.session_secret):
        raise HTTPException(status_code=400, detail="Invalid OAuth state.")

    token_payload = await exchange_code_for_token(code, settings)
    access_token = token_payload.get("access_token")
    if not access_token:
        raise HTTPException(status_code=400, detail="Missing access token.")

    user_payload = await fetch_user(access_token)
    guilds_payload = await fetch_user_guilds(access_token)
    guild_ids = [str(guild["id"]) for guild in guilds_payload if "id" in guild]
    if settings.discord_required_guild_id and settings.discord_required_guild_id not in guild_ids:
        raise HTTPException(status_code=403, detail="Not in required guild.")

    session_token = build_session_cookie(
        session=SessionData(
            user_id=str(user_payload["id"]),
            username=str(user_payload.get("username", "")),
            discriminator=user_payload.get("discriminator"),
            avatar=user_payload.get("avatar"),
            guild_ids=guild_ids,
            issued_at=datetime.now(timezone.utc),
        ),
        secret=settings.session_secret,
    )

    response = RedirectResponse(url="/")
    response.set_cookie(
        "jukebotx_session",
        session_token,
        httponly=True,
        secure=settings.env == "production",
        samesite="lax",
        max_age=settings.session_ttl_seconds,
    )
    return response


@app.post("/auth/logout")
async def logout() -> RedirectResponse:
    response = RedirectResponse(url="/")
    clear_session(response)
    return response


@app.get("/auth/me")
async def auth_me(session: SessionData = Depends(require_session)) -> dict[str, str]:
    return {
        "user_id": session.user_id,
        "display_name": session.display_name,
        "avatar": session.avatar or "",
    }


@app.get("/guilds/{guild_id}/queue", response_model=QueuePreviewResponse)
async def get_queue_preview(
    guild_id: int,
    limit: int = 10,
    session: SessionData = Depends(require_session),
    queue_repo: PostgresQueueRepository = Depends(get_queue_repo),
    track_repo: PostgresTrackRepository = Depends(get_track_repo),
) -> QueuePreviewResponse:
    ensure_guild_access(session, guild_id)
    use_case = GetQueuePreview(queue_repo=queue_repo)
    result = await use_case.execute(guild_id=guild_id, limit=limit)
    tracks = await asyncio.gather(*(require_track(track_repo, item.track_id) for item in result.items))
    items = [
        QueueItemSummary(
            id=item.id,
            position=item.position,
            status=item.status,
            requested_by=item.requested_by,
            created_at=item.created_at,
            updated_at=item.updated_at,
            track=TrackSummary.model_validate(track),
        )
        for item, track in zip(result.items, tracks)
    ]
    return QueuePreviewResponse(items=items)


@app.get("/guilds/{guild_id}/queue/next", response_model=NextQueueItemResponse)
async def get_next_queue_item(
    guild_id: int,
    session: SessionData = Depends(require_session),
    queue_repo: PostgresQueueRepository = Depends(get_queue_repo),
    track_repo: PostgresTrackRepository = Depends(get_track_repo),
) -> NextQueueItemResponse:
    ensure_guild_access(session, guild_id)
    queue_item = await queue_repo.get_next_unplayed(guild_id=guild_id)
    if queue_item is None:
        return NextQueueItemResponse(queue_item=None)
    track = await require_track(track_repo, queue_item.track_id)
    queue_item = QueueItemSummary(
        id=queue_item.id,
        position=queue_item.position,
        status=queue_item.status,
        requested_by=queue_item.requested_by,
        created_at=queue_item.created_at,
        updated_at=queue_item.updated_at,
        track=TrackSummary.model_validate(track),
    )
    return NextQueueItemResponse(queue_item=queue_item)


@app.get("/guilds/{guild_id}/channels/{channel_id}/session/tracks", response_model=list[SessionTrackResponse])
async def list_session_tracks(
    guild_id: int,
    channel_id: int,
    session: SessionData = Depends(require_session),
    submission_repo: PostgresSubmissionRepository = Depends(get_submission_repo),
) -> list[SessionTrackResponse]:
    ensure_guild_access(session, guild_id)
    tracks = await submission_repo.list_tracks_for_channel(guild_id=guild_id, channel_id=channel_id)
    return [SessionTrackResponse.model_validate(track) for track in tracks]


@app.get("/tracks/{track_id}", response_model=TrackSummary)
async def get_track(
    track_id: UUID,
    session: SessionData = Depends(require_session),
    track_repo: PostgresTrackRepository = Depends(get_track_repo),
) -> TrackSummary:
    track = await require_track(track_repo, track_id)
    return TrackSummary.model_validate(track)


@app.get("/tracks/{track_id}/audio")
async def get_track_audio(
    track_id: UUID,
    session: SessionData = Depends(require_session),
    track_repo: PostgresTrackRepository = Depends(get_track_repo),
) -> RedirectResponse:
    track = await require_track(track_repo, track_id)
    if track.mp3_url is None:
        raise HTTPException(status_code=404, detail="Track audio not available.")
    return RedirectResponse(url=track.mp3_url)
