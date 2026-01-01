from __future__ import annotations

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class ApiSettings:
    env: str
    discord_client_id: str
    discord_client_secret: str
    discord_redirect_uri: str
    discord_required_guild_id: str
    session_secret: str
    session_ttl_seconds: int


def load_api_settings() -> ApiSettings:
    return ApiSettings(
        env=os.getenv("ENV", "development"),
        discord_client_id=os.environ.get("DISCORD_OAUTH_CLIENT_ID", ""),
        discord_client_secret=os.environ.get("DISCORD_OAUTH_CLIENT_SECRET", ""),
        discord_redirect_uri=os.environ.get("DISCORD_OAUTH_REDIRECT_URI", ""),
        discord_required_guild_id=os.environ.get("DISCORD_GUILD_ID", ""),
        session_secret=os.environ.get("API_SESSION_SECRET", ""),
        session_ttl_seconds=int(os.environ.get("API_SESSION_TTL_SECONDS", "86400")),
    )
