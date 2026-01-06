import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from jukebotx_api.auth import (
    SessionData,
    build_login_redirect,
    build_session_cookie,
    build_state_token,
    clear_session,
    create_api_jwt,
    get_session_cookie,
    ensure_oauth_configured,
    exchange_activity_proof,
    exchange_code_for_token,
    fetch_user,
    fetch_user_guilds,
    parse_session_cookie,
    require_api_auth,
    validate_state_token,
)
from jukebotx_api.schemas import (
    NextQueueItemResponse,
    OpusStatusResponse,
    QueueItemSummary,
    QueuePreviewResponse,
    SessionTrackResponse,
    TrackSummary,
)
from jukebotx_infra.opus_cache import OpusCacheService
from jukebotx_infra.storage import OpusStorageConfig, OpusStorageService
from jukebotx_api.settings import ApiSettings, load_api_settings
from jukebotx_core.contracts import (
    EventEnvelope,
    NowPlayingDTO,
    QueueItemDTO,
    ReactionCountDTO,
    SessionStateDTO,
)
from jukebotx_core.ports.repositories import OpusJobCreate, QueueItem, Track
from jukebotx_core.use_cases.get_queue_preview import GetQueuePreview
from jukebotx_infra.db import async_session_factory
from jukebotx_infra.repos.opus_job_repo import PostgresOpusJobRepository
from jukebotx_infra.repos.jam_session_repo import PostgresJamSessionRepository
from jukebotx_infra.repos.queue_repo import PostgresQueueRepository
from jukebotx_infra.repos.session_reaction_repo import PostgresSessionReactionRepository
from jukebotx_infra.repos.submission_repo import PostgresSubmissionRepository
from jukebotx_infra.repos.track_repo import PostgresTrackRepository

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="JukeBotx API")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
logger = logging.getLogger(__name__)


class DiscordActivityExchangeRequest(BaseModel):
    proof: str


class DiscordActivityExchangeResponse(BaseModel):
    token: str
    token_type: str
    expires_in: int
    user_id: str
    username: str
    guild_ids: list[str]


def get_queue_repo() -> PostgresQueueRepository:
    return PostgresQueueRepository(async_session_factory)


def get_jam_session_repo() -> PostgresJamSessionRepository:
    return PostgresJamSessionRepository(async_session_factory)


def get_session_reaction_repo() -> PostgresSessionReactionRepository:
    return PostgresSessionReactionRepository(async_session_factory)


def get_track_repo() -> PostgresTrackRepository:
    return PostgresTrackRepository(async_session_factory)


def get_submission_repo() -> PostgresSubmissionRepository:
    return PostgresSubmissionRepository(async_session_factory)


def get_opus_job_repo() -> PostgresOpusJobRepository:
    return PostgresOpusJobRepository(async_session_factory)


def get_opus_cache_service(settings: ApiSettings = Depends(load_api_settings)) -> OpusCacheService:
    cache_dir = Path(settings.opus_cache_dir)
    if not cache_dir.is_absolute():
        cache_dir = BASE_DIR / cache_dir
    return OpusCacheService(cache_dir=cache_dir, ttl_seconds=settings.opus_cache_ttl_seconds)


def get_opus_storage_service(settings: ApiSettings = Depends(load_api_settings)) -> OpusStorageService:
    config = OpusStorageConfig(
        provider=settings.opus_storage_provider,
        bucket=settings.opus_storage_bucket,
        prefix=settings.opus_storage_prefix,
        region=settings.opus_storage_region,
        endpoint_url=settings.opus_storage_endpoint_url,
        access_key_id=settings.opus_storage_access_key_id,
        secret_access_key=settings.opus_storage_secret_access_key,
        public_base_url=settings.opus_storage_public_base_url,
        signed_url_ttl_seconds=settings.opus_storage_signed_url_ttl_seconds,
        ttl_seconds=settings.opus_storage_ttl_seconds,
    )
    return OpusStorageService(config)


def ensure_guild_access(session: SessionData, guild_id: int) -> None:
    if str(guild_id) not in session.guild_ids:
        raise HTTPException(status_code=403, detail="Forbidden for this guild.")


async def require_track(track_repo: PostgresTrackRepository, track_id: UUID) -> Track:
    try:
        return await track_repo.get_by_id(track_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def build_queue_item_dto(item: QueueItem, track: Track) -> QueueItemDTO:
    return QueueItemDTO(
        id=item.id,
        position=item.position,
        status=item.status,
        requested_by=item.requested_by,
        created_at=item.created_at,
        updated_at=item.updated_at,
        track_id=track.id,
        title=track.title,
        artist_display=track.artist_display,
        image_url=track.image_url,
        mp3_url=track.mp3_url,
        opus_url=track.opus_url,
    )


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


@app.post("/v1/auth/discord/exchange", response_model=DiscordActivityExchangeResponse)
async def discord_activity_exchange(
    payload: DiscordActivityExchangeRequest,
    settings: ApiSettings = Depends(load_api_settings),
) -> DiscordActivityExchangeResponse:
    token_payload = await exchange_activity_proof(payload.proof, settings)
    access_token = token_payload.get("access_token")
    if not access_token:
        raise HTTPException(status_code=400, detail="Missing access token.")

    user_payload = await fetch_user(access_token)
    guilds_payload = await fetch_user_guilds(access_token)
    guild_ids = [str(guild["id"]) for guild in guilds_payload if "id" in guild]
    if settings.discord_required_guild_id and settings.discord_required_guild_id not in guild_ids:
        raise HTTPException(status_code=403, detail="Not in required guild.")

    session = SessionData(
        user_id=str(user_payload["id"]),
        username=str(user_payload.get("username", "")),
        discriminator=user_payload.get("discriminator"),
        avatar=user_payload.get("avatar"),
        guild_ids=guild_ids,
        issued_at=datetime.now(timezone.utc),
    )
    token = create_api_jwt(session, settings.jwt_secret, settings.jwt_ttl_seconds)
    return DiscordActivityExchangeResponse(
        token=token,
        token_type="Bearer",
        expires_in=settings.jwt_ttl_seconds,
        user_id=session.user_id,
        username=session.display_name,
        guild_ids=session.guild_ids,
    )


@app.post("/auth/logout")
async def logout() -> RedirectResponse:
    response = RedirectResponse(url="/")
    clear_session(response)
    return response


@app.get("/auth/me")
async def auth_me(session: SessionData = Depends(require_api_auth)) -> dict[str, str]:
    return {
        "user_id": session.user_id,
        "display_name": session.display_name,
        "avatar": session.avatar or "",
    }


@app.get("/guilds/{guild_id}/queue", response_model=QueuePreviewResponse)
async def get_queue_preview(
    guild_id: int,
    limit: int = 10,
    session_id: UUID | None = None,
    session: SessionData = Depends(require_api_auth),
    queue_repo: PostgresQueueRepository = Depends(get_queue_repo),
    track_repo: PostgresTrackRepository = Depends(get_track_repo),
) -> QueuePreviewResponse:
    ensure_guild_access(session, guild_id)
    use_case = GetQueuePreview(queue_repo=queue_repo)
    result = await use_case.execute(guild_id=guild_id, session_id=session_id, limit=limit)
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


@app.get(
    "/guilds/{guild_id}/channels/{channel_id}/activity/state",
    response_model=EventEnvelope[SessionStateDTO],
)
async def get_activity_state(
    guild_id: int,
    channel_id: int,
    limit: int = 10,
    session: SessionData = Depends(require_api_auth),
    queue_repo: PostgresQueueRepository = Depends(get_queue_repo),
    track_repo: PostgresTrackRepository = Depends(get_track_repo),
    jam_session_repo: PostgresJamSessionRepository = Depends(get_jam_session_repo),
    session_reaction_repo: PostgresSessionReactionRepository = Depends(get_session_reaction_repo),
) -> EventEnvelope[SessionStateDTO]:
    ensure_guild_access(session, guild_id)
    jam_session = await jam_session_repo.get_active_for_guild(guild_id=guild_id)
    if jam_session and jam_session.channel_id != channel_id:
        jam_session = None

    session_id = jam_session.id if jam_session else None
    queue_items: list[QueueItemDTO] = []
    reactions: list[ReactionCountDTO] = []
    now_playing: NowPlayingDTO | None = None

    if session_id:
        queue_domain = await queue_repo.preview(guild_id=guild_id, session_id=session_id, limit=limit)
        tracks = await asyncio.gather(*(require_track(track_repo, item.track_id) for item in queue_domain))
        queue_items = [build_queue_item_dto(item, track) for item, track in zip(queue_domain, tracks)]

        next_item = await queue_repo.get_next_unplayed(guild_id=guild_id, session_id=session_id)
        if next_item:
            next_track = await require_track(track_repo, next_item.track_id)
            now_playing = NowPlayingDTO(queue_item=build_queue_item_dto(next_item, next_track))

        reactions_domain = await session_reaction_repo.list_for_session(session_id=session_id)
        reaction_counts: dict[tuple[UUID, str], int] = {}
        for reaction in reactions_domain:
            key = (reaction.track_id, reaction.reaction_type.value)
            reaction_counts[key] = reaction_counts.get(key, 0) + 1
        reactions = [
            ReactionCountDTO(track_id=track_id, reaction_type=reaction_type, count=count)
            for (track_id, reaction_type), count in reaction_counts.items()
        ]

    state = SessionStateDTO(
        session_id=session_id,
        guild_id=guild_id,
        channel_id=jam_session.channel_id if jam_session else channel_id,
        status=jam_session.status.value if jam_session else None,
        created_at=jam_session.created_at if jam_session else None,
        updated_at=jam_session.updated_at if jam_session else None,
        ended_at=jam_session.ended_at if jam_session else None,
        now_playing=now_playing,
        queue=queue_items,
        reactions=reactions,
    )
    return EventEnvelope(event_type="session.state", data=state)


@app.get(
    "/guilds/{guild_id}/channels/{channel_id}/activity/queue",
    response_model=EventEnvelope[list[QueueItemDTO]],
)
async def get_activity_queue(
    guild_id: int,
    channel_id: int,
    limit: int = 10,
    session: SessionData = Depends(require_api_auth),
    queue_repo: PostgresQueueRepository = Depends(get_queue_repo),
    track_repo: PostgresTrackRepository = Depends(get_track_repo),
    jam_session_repo: PostgresJamSessionRepository = Depends(get_jam_session_repo),
) -> EventEnvelope[list[QueueItemDTO]]:
    ensure_guild_access(session, guild_id)
    jam_session = await jam_session_repo.get_active_for_guild(guild_id=guild_id)
    if not jam_session or jam_session.channel_id != channel_id:
        return EventEnvelope(event_type="queue.snapshot", data=[])

    queue_domain = await queue_repo.preview(guild_id=guild_id, session_id=jam_session.id, limit=limit)
    tracks = await asyncio.gather(*(require_track(track_repo, item.track_id) for item in queue_domain))
    queue_items = [build_queue_item_dto(item, track) for item, track in zip(queue_domain, tracks)]
    return EventEnvelope(event_type="queue.snapshot", data=queue_items)


@app.get(
    "/guilds/{guild_id}/channels/{channel_id}/activity/now-playing",
    response_model=EventEnvelope[NowPlayingDTO],
)
async def get_activity_now_playing(
    guild_id: int,
    channel_id: int,
    session: SessionData = Depends(require_api_auth),
    queue_repo: PostgresQueueRepository = Depends(get_queue_repo),
    track_repo: PostgresTrackRepository = Depends(get_track_repo),
    jam_session_repo: PostgresJamSessionRepository = Depends(get_jam_session_repo),
) -> EventEnvelope[NowPlayingDTO]:
    ensure_guild_access(session, guild_id)
    jam_session = await jam_session_repo.get_active_for_guild(guild_id=guild_id)
    if not jam_session or jam_session.channel_id != channel_id:
        return EventEnvelope(event_type="now_playing", data=NowPlayingDTO(queue_item=None))

    next_item = await queue_repo.get_next_unplayed(guild_id=guild_id, session_id=jam_session.id)
    if not next_item:
        return EventEnvelope(event_type="now_playing", data=NowPlayingDTO(queue_item=None))

    track = await require_track(track_repo, next_item.track_id)
    now_playing = NowPlayingDTO(queue_item=build_queue_item_dto(next_item, track))
    return EventEnvelope(event_type="now_playing", data=now_playing)


@app.get(
    "/guilds/{guild_id}/channels/{channel_id}/activity/reactions",
    response_model=EventEnvelope[list[ReactionCountDTO]],
)
async def get_activity_reactions(
    guild_id: int,
    channel_id: int,
    session: SessionData = Depends(require_api_auth),
    jam_session_repo: PostgresJamSessionRepository = Depends(get_jam_session_repo),
    session_reaction_repo: PostgresSessionReactionRepository = Depends(get_session_reaction_repo),
) -> EventEnvelope[list[ReactionCountDTO]]:
    ensure_guild_access(session, guild_id)
    jam_session = await jam_session_repo.get_active_for_guild(guild_id=guild_id)
    if not jam_session or jam_session.channel_id != channel_id:
        return EventEnvelope(event_type="reactions.snapshot", data=[])

    reactions_domain = await session_reaction_repo.list_for_session(session_id=jam_session.id)
    reaction_counts: dict[tuple[UUID, str], int] = {}
    for reaction in reactions_domain:
        key = (reaction.track_id, reaction.reaction_type.value)
        reaction_counts[key] = reaction_counts.get(key, 0) + 1
    reactions = [
        ReactionCountDTO(track_id=track_id, reaction_type=reaction_type, count=count)
        for (track_id, reaction_type), count in reaction_counts.items()
    ]
    return EventEnvelope(event_type="reactions.snapshot", data=reactions)


@app.get("/guilds/{guild_id}/queue/next", response_model=NextQueueItemResponse)
async def get_next_queue_item(
    guild_id: int,
    session_id: UUID | None = None,
    session: SessionData = Depends(require_api_auth),
    queue_repo: PostgresQueueRepository = Depends(get_queue_repo),
    track_repo: PostgresTrackRepository = Depends(get_track_repo),
) -> NextQueueItemResponse:
    ensure_guild_access(session, guild_id)
    queue_item = await queue_repo.get_next_unplayed(guild_id=guild_id, session_id=session_id)
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
    session: SessionData = Depends(require_api_auth),
    submission_repo: PostgresSubmissionRepository = Depends(get_submission_repo),
) -> list[SessionTrackResponse]:
    ensure_guild_access(session, guild_id)
    tracks = await submission_repo.list_tracks_for_channel(guild_id=guild_id, channel_id=channel_id)
    return [SessionTrackResponse.model_validate(track) for track in tracks]


@app.get("/tracks/{track_id}", response_model=TrackSummary)
async def get_track(
    track_id: UUID,
    session: SessionData = Depends(require_api_auth),
    track_repo: PostgresTrackRepository = Depends(get_track_repo),
) -> TrackSummary:
    track = await require_track(track_repo, track_id)
    return TrackSummary.model_validate(track)


@app.get("/tracks/{track_id}/audio")
async def get_track_audio(
    track_id: UUID,
    session: SessionData = Depends(require_api_auth),
    track_repo: PostgresTrackRepository = Depends(get_track_repo),
) -> RedirectResponse:
    track = await require_track(track_repo, track_id)
    if track.mp3_url is None:
        raise HTTPException(status_code=404, detail="Track audio not available.")
    return RedirectResponse(url=track.mp3_url)


@app.get("/tracks/{track_id}/opus", response_model=None)
async def get_track_opus(
    track_id: UUID,
    track_repo: PostgresTrackRepository = Depends(get_track_repo),
    opus_cache: OpusCacheService = Depends(get_opus_cache_service),
    opus_storage: OpusStorageService = Depends(get_opus_storage_service),
    opus_jobs: PostgresOpusJobRepository = Depends(get_opus_job_repo),
):
    track = await require_track(track_repo, track_id)
    if track.mp3_url is None:
        raise HTTPException(status_code=404, detail="Track audio not available.")

    if track.opus_status == "completed":
        if opus_storage.is_enabled:
            object_key = track.opus_path or ""
            if object_key and opus_storage.is_fresh(object_key=object_key):
                return RedirectResponse(url=opus_storage.get_access_url(object_key=object_key))
            if track.opus_url:
                return RedirectResponse(url=track.opus_url)
        else:
            opus_path_value = track.opus_path or str(opus_cache.cache_path(track_id=track_id))
            opus_path = Path(opus_path_value)
            if opus_path.exists():
                return FileResponse(opus_path, media_type="audio/opus", filename=f"{track_id}.opus")

    await opus_jobs.enqueue(data=OpusJobCreate(track_id=track_id, mp3_url=track.mp3_url))
    return RedirectResponse(url=track.mp3_url)


@app.get("/tracks/{track_id}/opus/status", response_model=OpusStatusResponse)
async def get_track_opus_status(
    track_id: UUID,
    track_repo: PostgresTrackRepository = Depends(get_track_repo),
    opus_cache: OpusCacheService = Depends(get_opus_cache_service),
    opus_storage: OpusStorageService = Depends(get_opus_storage_service),
    opus_jobs: PostgresOpusJobRepository = Depends(get_opus_job_repo),
) -> OpusStatusResponse:
    track = await require_track(track_repo, track_id)
    if track.mp3_url is None:
        raise HTTPException(status_code=404, detail="Track audio not available.")

    if track.opus_status == "completed":
        if opus_storage.is_enabled:
            object_key = track.opus_path or ""
            if object_key and opus_storage.is_fresh(object_key=object_key):
                return OpusStatusResponse(track_id=track_id, ready=True, status="ready")
            if track.opus_url:
                return OpusStatusResponse(track_id=track_id, ready=True, status="ready")
        else:
            opus_path_value = track.opus_path or str(opus_cache.cache_path(track_id=track_id))
            opus_path = Path(opus_path_value)
            if opus_path.exists():
                return OpusStatusResponse(track_id=track_id, ready=True, status="ready")
    if track.opus_status == "failed":
        return OpusStatusResponse(track_id=track_id, ready=False, status="failed")

    job = await opus_jobs.enqueue(data=OpusJobCreate(track_id=track_id, mp3_url=track.mp3_url))
    return OpusStatusResponse(track_id=track_id, ready=False, status=job.status)
