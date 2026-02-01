from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import base64
import hashlib
import hmac
import json
import secrets
from typing import Any

from fastapi import Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
import httpx

from jukebotx_api.settings import ApiSettings, load_api_settings


@dataclass(frozen=True)
class SessionData:
    user_id: str
    username: str
    discriminator: str | None
    avatar: str | None
    guild_ids: list[str]
    issued_at: datetime

    @property
    def display_name(self) -> str:
        if self.discriminator and self.discriminator != "0":
            return f"{self.username}#{self.discriminator}"
        return self.username


OAUTH_AUTHORIZE_URL = "https://discord.com/api/oauth2/authorize"
OAUTH_TOKEN_URL = "https://discord.com/api/oauth2/token"
DISCORD_API_BASE = "https://discord.com/api"


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("utf-8")


def _b64decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def _sign_payload(payload: dict[str, Any], secret: str) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    body = _b64encode(raw)
    signature = hmac.new(secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).digest()
    return f"{body}.{_b64encode(signature)}"


def _unsign_payload(token: str, secret: str) -> dict[str, Any] | None:
    if "." not in token:
        return None
    body, sig = token.split(".", 1)
    expected = hmac.new(secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).digest()
    if not hmac.compare_digest(_b64encode(expected), sig):
        return None
    try:
        return json.loads(_b64decode(body))
    except json.JSONDecodeError:
        return None


def build_state_token(secret: str) -> str:
    payload = {
        "nonce": secrets.token_urlsafe(16),
        "issued_at": datetime.now(timezone.utc).isoformat(),
    }
    return _sign_payload(payload, secret)


def validate_state_token(token: str, secret: str) -> bool:
    payload = _unsign_payload(token, secret)
    if payload is None:
        return False
    try:
        issued_at = datetime.fromisoformat(payload["issued_at"])
    except (KeyError, ValueError, TypeError):
        return False
    if datetime.now(timezone.utc) - issued_at > timedelta(minutes=10):
        return False
    return True


def build_session_cookie(session: SessionData, secret: str) -> str:
    payload = {
        "user_id": session.user_id,
        "username": session.username,
        "discriminator": session.discriminator,
        "avatar": session.avatar,
        "guild_ids": session.guild_ids,
        "issued_at": session.issued_at.isoformat(),
    }
    return _sign_payload(payload, secret)


def parse_session_cookie(token: str, secret: str) -> SessionData | None:
    payload = _unsign_payload(token, secret)
    if payload is None:
        return None
    try:
        issued_at = datetime.fromisoformat(payload["issued_at"])
        return SessionData(
            user_id=str(payload["user_id"]),
            username=str(payload["username"]),
            discriminator=payload.get("discriminator"),
            avatar=payload.get("avatar"),
            guild_ids=[str(gid) for gid in payload.get("guild_ids", [])],
            issued_at=issued_at,
        )
    except (KeyError, ValueError, TypeError):
        return None


def get_session_cookie(request: Request) -> str | None:
    return request.cookies.get("jukebotx_session")


def clear_session(response: Response) -> None:
    response.delete_cookie("jukebotx_session")


async def exchange_code_for_token(code: str, settings: ApiSettings) -> dict[str, Any]:
    data = {
        "client_id": settings.discord_client_id,
        "client_secret": settings.discord_client_secret,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.discord_redirect_uri,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(OAUTH_TOKEN_URL, data=data, headers=headers)
        resp.raise_for_status()
        return resp.json()


async def fetch_user(access_token: str) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(f"{DISCORD_API_BASE}/users/@me", headers=headers)
        resp.raise_for_status()
        return resp.json()


async def fetch_user_guilds(access_token: str) -> list[dict[str, Any]]:
    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(f"{DISCORD_API_BASE}/users/@me/guilds", headers=headers)
        resp.raise_for_status()
        return resp.json()


def ensure_oauth_configured(settings: ApiSettings) -> None:
    missing = [
        name
        for name, value in {
            "DISCORD_OAUTH_CLIENT_ID": settings.discord_client_id,
            "DISCORD_OAUTH_CLIENT_SECRET": settings.discord_client_secret,
            "DISCORD_OAUTH_REDIRECT_URI": settings.discord_redirect_uri,
            "DISCORD_GUILD_ID": settings.discord_required_guild_id,
            "API_SESSION_SECRET": settings.session_secret,
        }.items()
        if not value
    ]
    if missing:
        missing_list = ", ".join(missing)
        raise HTTPException(
            status_code=500,
            detail=f"OAuth configuration incomplete: {missing_list}",
        )


def build_login_redirect(settings: ApiSettings, state_token: str) -> RedirectResponse:
    params = {
        "client_id": settings.discord_client_id,
        "redirect_uri": settings.discord_redirect_uri,
        "response_type": "code",
        "scope": "identify guilds",
        "state": state_token,
        "prompt": "consent",
    }
    query = httpx.QueryParams(params).encode()
    return RedirectResponse(f"{OAUTH_AUTHORIZE_URL}?{query}")


def require_session(
    request: Request,
    settings: ApiSettings = Depends(load_api_settings),
) -> SessionData:
    ensure_oauth_configured(settings)
    token = get_session_cookie(request)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated.")
    session = parse_session_cookie(token, settings.session_secret)
    if session is None:
        raise HTTPException(status_code=401, detail="Invalid session.")
    max_age = timedelta(seconds=settings.session_ttl_seconds)
    if datetime.now(timezone.utc) - session.issued_at > max_age:
        raise HTTPException(status_code=401, detail="Session expired.")
    if settings.discord_required_guild_id and settings.discord_required_guild_id not in session.guild_ids:
        raise HTTPException(status_code=403, detail="Not in required guild.")
    return session
