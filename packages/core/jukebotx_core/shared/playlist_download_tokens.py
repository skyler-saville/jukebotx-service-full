from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import base64
import hashlib
import hmac
import json
from typing import Any


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


@dataclass(frozen=True)
class PlaylistArchiveDownloadClaims:
    object_key: str
    filename: str
    issued_at: datetime
    expires_at: datetime


def build_playlist_archive_download_token(
    *,
    object_key: str,
    filename: str,
    secret: str,
    ttl_seconds: int,
) -> str:
    issued_at = datetime.now(timezone.utc)
    expires_at = issued_at + timedelta(seconds=max(ttl_seconds, 1))
    payload = {
        "type": "playlist_archive_download",
        "object_key": object_key,
        "filename": filename,
        "issued_at": issued_at.isoformat(),
        "expires_at": expires_at.isoformat(),
    }
    return _sign_payload(payload, secret)


def parse_playlist_archive_download_token(
    token: str,
    secret: str,
) -> PlaylistArchiveDownloadClaims | None:
    payload = _unsign_payload(token, secret)
    if payload is None or payload.get("type") != "playlist_archive_download":
        return None

    try:
        claims = PlaylistArchiveDownloadClaims(
            object_key=str(payload["object_key"]),
            filename=str(payload["filename"]),
            issued_at=datetime.fromisoformat(payload["issued_at"]),
            expires_at=datetime.fromisoformat(payload["expires_at"]),
        )
    except (KeyError, TypeError, ValueError):
        return None

    if claims.expires_at <= datetime.now(timezone.utc):
        return None
    return claims
