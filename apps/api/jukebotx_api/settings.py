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
    discord_activity_client_id: str
    discord_activity_client_secret: str
    discord_activity_redirect_uri: str
    session_secret: str
    session_ttl_seconds: int
    jwt_secret: str
    jwt_ttl_seconds: int
    opus_cache_dir: str
    opus_cache_ttl_seconds: int
    opus_storage_provider: str
    opus_storage_bucket: str
    opus_storage_prefix: str
    opus_storage_region: str
    opus_storage_endpoint_url: str
    opus_storage_access_key_id: str
    opus_storage_secret_access_key: str
    opus_storage_public_base_url: str
    opus_storage_signed_url_ttl_seconds: int
    opus_storage_ttl_seconds: int


def load_api_settings() -> ApiSettings:
    return ApiSettings(
        env=os.getenv("ENV", "development"),
        discord_client_id=os.environ.get("DISCORD_OAUTH_CLIENT_ID", ""),
        discord_client_secret=os.environ.get("DISCORD_OAUTH_CLIENT_SECRET", ""),
        discord_redirect_uri=os.environ.get("DISCORD_OAUTH_REDIRECT_URI", ""),
        discord_required_guild_id=os.environ.get("DISCORD_GUILD_ID", ""),
        discord_activity_client_id=os.environ.get("DISCORD_ACTIVITY_CLIENT_ID", ""),
        discord_activity_client_secret=os.environ.get("DISCORD_ACTIVITY_CLIENT_SECRET", ""),
        discord_activity_redirect_uri=os.environ.get("DISCORD_ACTIVITY_REDIRECT_URI", ""),
        session_secret=os.environ.get("API_SESSION_SECRET", ""),
        session_ttl_seconds=int(os.environ.get("API_SESSION_TTL_SECONDS", "86400")),
        jwt_secret=os.environ.get("API_JWT_SECRET", ""),
        jwt_ttl_seconds=int(os.environ.get("API_JWT_TTL_SECONDS", "900")),
        opus_cache_dir=os.environ.get("OPUS_CACHE_DIR", "static/opus"),
        opus_cache_ttl_seconds=int(os.environ.get("OPUS_CACHE_TTL_SECONDS", "604800")),
        opus_storage_provider=os.environ.get("OPUS_STORAGE_PROVIDER", "s3"),
        opus_storage_bucket=os.environ.get("OPUS_STORAGE_BUCKET", ""),
        opus_storage_prefix=os.environ.get("OPUS_STORAGE_PREFIX", "opus"),
        opus_storage_region=os.environ.get("OPUS_STORAGE_REGION", ""),
        opus_storage_endpoint_url=os.environ.get("OPUS_STORAGE_ENDPOINT_URL", ""),
        opus_storage_access_key_id=os.environ.get("OPUS_STORAGE_ACCESS_KEY_ID", ""),
        opus_storage_secret_access_key=os.environ.get("OPUS_STORAGE_SECRET_ACCESS_KEY", ""),
        opus_storage_public_base_url=os.environ.get("OPUS_STORAGE_PUBLIC_BASE_URL", ""),
        opus_storage_signed_url_ttl_seconds=int(
            os.environ.get("OPUS_STORAGE_SIGNED_URL_TTL_SECONDS", "900")
        ),
        opus_storage_ttl_seconds=int(os.environ.get("OPUS_STORAGE_TTL_SECONDS", "604800")),
    )
