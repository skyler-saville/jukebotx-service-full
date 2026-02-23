from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
from typing import Any


_EVENT_FIELDS = (
    "event_name",
    "guild_id",
    "channel_id",
    "user_id",
    "message_id",
    "error_type",
)


class JsonLogFormatter(logging.Formatter):
    """Emit structured JSON logs with stable event context fields."""

    def format(self, record: logging.LogRecord) -> str:
        error_type = getattr(record, "error_type", None)
        if error_type is None and record.exc_info and record.exc_info[0] is not None:
            error_type = record.exc_info[0].__name__

        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "error_type": error_type,
        }
        for field in _EVENT_FIELDS:
            if field == "error_type":
                continue
            payload[field] = getattr(record, field, None)

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False)


class EventLoggerAdapter(logging.LoggerAdapter):
    """LoggerAdapter that always includes standard event context keys."""

    def process(self, msg: str, kwargs: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        extra = kwargs.get("extra", {})
        merged_extra = {**self.extra, **extra}
        for field in _EVENT_FIELDS:
            merged_extra.setdefault(field, None)
        kwargs["extra"] = merged_extra
        return msg, kwargs


def get_event_logger(
    logger: logging.Logger,
    *,
    event_name: str,
    guild_id: int | None = None,
    channel_id: int | None = None,
    user_id: int | None = None,
    message_id: int | None = None,
    error_type: str | None = None,
) -> EventLoggerAdapter:
    return EventLoggerAdapter(
        logger,
        {
            "event_name": event_name,
            "guild_id": guild_id,
            "channel_id": channel_id,
            "user_id": user_id,
            "message_id": message_id,
            "error_type": error_type,
        },
    )


def _parse_log_level(value: str, *, default: int) -> int:
    candidate = getattr(logging, value.upper(), None)
    if isinstance(candidate, int):
        return candidate
    return default


def configure_logging() -> None:
    """Configure process logging once, with env-configurable levels."""
    if getattr(configure_logging, "_configured", False):
        return

    root_level_name = os.getenv("BOT_LOG_LEVEL", "INFO")
    root_level = _parse_log_level(root_level_name, default=logging.INFO)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(root_level)

    handler = logging.StreamHandler()
    handler.setFormatter(JsonLogFormatter())
    root_logger.addHandler(handler)

    audio_level_name = os.getenv("BOT_AUDIO_LOG_LEVEL", "WARNING")
    audio_level = _parse_log_level(audio_level_name, default=logging.WARNING)
    logging.getLogger("jukebotx_bot.discord.audio").setLevel(audio_level)

    configure_logging._configured = True
