from __future__ import annotations

from typing import Any
import logging

import httpx

from jukebotx_bot.discord.session import Track

logger = logging.getLogger(__name__)


def serialize_track(track: Track) -> dict[str, Any]:
    return {
        "title": track.title,
        "artist_display": track.artist_display,
        "requester_id": track.requester_id,
        "requester_name": track.requester_name,
        "media_url": track.media_url,
        "page_url": track.page_url,
        "duration_seconds": track.duration_seconds,
    }


def build_queue_payload(queue: list[Track], now_playing: Track | None) -> dict[str, Any]:
    preview = [serialize_track(item) for item in queue[:5]]
    payload: dict[str, Any] = {
        "queue_size": len(queue),
        "queue_preview": preview,
    }
    if now_playing is not None:
        payload["now_playing"] = serialize_track(now_playing)
    return payload


class InternalApiClient:
    def __init__(self, base_url: str | None, token: str | None) -> None:
        self._base_url = base_url
        self._token = token

    def _is_configured(self) -> bool:
        return bool(self._base_url and self._token)

    async def post_playback_update(
        self,
        *,
        guild_id: int,
        channel_id: int | None,
        event_type: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        if not self._is_configured():
            logger.debug("Internal API client not configured; skipping %s update.", event_type)
            return
        url = f"{self._base_url.rstrip('/')}/v1/internal/playback-updates"
        payload = {
            "guild_id": guild_id,
            "channel_id": channel_id,
            "event_type": event_type,
            "data": data or {},
        }
        headers = {"Authorization": f"Bearer {self._token}"}
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code >= 400:
                logger.warning(
                    "Internal API update failed (%s): %s",
                    resp.status_code,
                    resp.text,
                )
        except Exception as exc:
            logger.warning("Internal API update failed: %s", exc)
