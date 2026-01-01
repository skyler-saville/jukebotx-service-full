from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse

from jukebotx_api.auth import (
    SessionData,
    build_login_redirect,
    build_session_cookie,
    build_state_token,
    clear_session,
    ensure_oauth_configured,
    exchange_code_for_token,
    fetch_user,
    fetch_user_guilds,
    require_session,
    validate_state_token,
)
from jukebotx_api.settings import ApiSettings, load_api_settings

app = FastAPI(title="JukeBotx API")

@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


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
