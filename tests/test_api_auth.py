from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

import pytest
from fastapi import HTTPException
from starlette.requests import Request

ROOT = Path(__file__).resolve().parents[1]
sys.path.extend([str(ROOT / "apps" / "api")])

from jukebotx_api.auth import SessionData, build_session_cookie, parse_session_cookie, require_session
from jukebotx_api.settings import ApiSettings


def _make_settings(**overrides: object) -> ApiSettings:
    defaults = {
        "env": "development",
        "discord_client_id": "client",
        "discord_client_secret": "secret",
        "discord_redirect_uri": "http://localhost/callback",
        "discord_required_guild_id": "123",
        "session_secret": "session-secret",
        "session_ttl_seconds": 3600,
        "opus_cache_dir": "cache",
        "opus_cache_ttl_seconds": 60,
        "opus_storage_provider": "s3",
        "opus_storage_bucket": "bucket",
        "opus_storage_prefix": "opus",
        "opus_storage_region": "",
        "opus_storage_endpoint_url": "",
        "opus_storage_access_key_id": "",
        "opus_storage_secret_access_key": "",
        "opus_storage_public_base_url": "",
        "opus_storage_signed_url_ttl_seconds": 60,
        "opus_storage_ttl_seconds": 60,
    }
    defaults.update(overrides)
    return ApiSettings(**defaults)


def _make_request(cookie_value: str | None) -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if cookie_value is not None:
        headers.append((b"cookie", f"jukebotx_session={cookie_value}".encode("utf-8")))
    return Request({"type": "http", "headers": headers})


def test_parse_session_cookie_returns_none_for_tampered_token() -> None:
    now = datetime.now(timezone.utc)
    token = build_session_cookie(
        SessionData(
            user_id="1",
            username="user",
            discriminator="1234",
            avatar=None,
            guild_ids=["123"],
            issued_at=now,
        ),
        "top-secret",
    )

    tampered = f"{token}oops"
    assert parse_session_cookie(tampered, "top-secret") is None


def test_require_session_returns_session_for_valid_cookie() -> None:
    now = datetime.now(timezone.utc)
    settings = _make_settings()
    token = build_session_cookie(
        SessionData(
            user_id="42",
            username="alice",
            discriminator="0",
            avatar="img",
            guild_ids=["123", "999"],
            issued_at=now,
        ),
        settings.session_secret,
    )

    session = require_session(_make_request(token), settings=settings)
    assert session.user_id == "42"
    assert session.display_name == "alice"


def test_require_session_raises_when_cookie_expired() -> None:
    old_issued_at = datetime.now(timezone.utc) - timedelta(seconds=61)
    settings = _make_settings(session_ttl_seconds=60)
    token = build_session_cookie(
        SessionData(
            user_id="99",
            username="old-user",
            discriminator=None,
            avatar=None,
            guild_ids=["123"],
            issued_at=old_issued_at,
        ),
        settings.session_secret,
    )

    with pytest.raises(HTTPException, match="Session expired") as exc:
        require_session(_make_request(token), settings=settings)

    assert exc.value.status_code == 401
