# apps/bot/jukebotx_bot/settings.py
from __future__ import annotations

from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


class BotSettings(BaseSettings):
    """
    Bot configuration loaded from environment variables and (in local dev) a .env file.

    Required:
      - ENV: "development" or "production" (you can add "staging" later)

    Tokens:
      - DEV_DISCORD_TOKEN: used when ENV=development
      - DISCORD_TOKEN: used otherwise
    """

    env: str = Field(..., alias="ENV")

    discord_token: str | None = Field(default=None, alias="DISCORD_TOKEN")
    dev_discord_token: str | None = Field(default=None, alias="DEV_DISCORD_TOKEN")

    jam_session_channel_id: int | None = Field(
        default=None, alias="JAM_SESSION_CHANNEL_ID"
    )
    jam_session_role_id: int | None = Field(default=None, alias="JAM_SESSION_ROLE_ID")
    web_base_url: str | None = Field(default=None, alias="WEB_BASE_URL")
    opus_api_base_url: str | None = Field(default=None, alias="OPUS_API_BASE_URL")
    master_user_id: int | None = Field(default=None, alias="MASTER_USER_ID")
    master_dm_min_interval_seconds: int = Field(
        default=600,
        alias="MASTER_DM_MIN_INTERVAL_SECONDS",
    )
    master_dm_burst_threshold: int = Field(
        default=5,
        alias="MASTER_DM_BURST_THRESHOLD",
    )
    master_dm_burst_window_seconds: int = Field(
        default=120,
        alias="MASTER_DM_BURST_WINDOW_SECONDS",
    )
    media_storage_provider: str = Field(default="s3", alias="MEDIA_STORAGE_PROVIDER")
    media_storage_bucket: str = Field(default="", alias="MEDIA_STORAGE_BUCKET")
    media_storage_prefix: str = Field(default="media-gifs", alias="MEDIA_STORAGE_PREFIX")
    media_storage_region: str = Field(default="", alias="MEDIA_STORAGE_REGION")
    media_storage_endpoint_url: str = Field(default="", alias="MEDIA_STORAGE_ENDPOINT_URL")
    media_storage_access_key_id: str = Field(default="", alias="MEDIA_STORAGE_ACCESS_KEY_ID")
    media_storage_secret_access_key: str = Field(default="", alias="MEDIA_STORAGE_SECRET_ACCESS_KEY")
    media_storage_public_base_url: str = Field(default="", alias="MEDIA_STORAGE_PUBLIC_BASE_URL")
    media_ffmpeg_path: str = Field(default="ffmpeg", alias="MEDIA_FFMPEG_PATH")

    # Pydantic v2 configuration
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        case_sensitive=False,
    )

    @property
    def active_discord_token(self) -> str:
        env_norm = self.env.strip().lower()

        if env_norm == "development":
            if not self.dev_discord_token:
                raise RuntimeError("ENV=development but DEV_DISCORD_TOKEN is not set")
            return self.dev_discord_token

        if not self.discord_token:
            raise RuntimeError("ENV is not development but DISCORD_TOKEN is not set")
        return self.discord_token


def load_bot_settings() -> BotSettings:
    """
    Load and validate bot settings. Raises a RuntimeError with a readable message on failure.
    """
    try:
        return BotSettings()
    except ValidationError as exc:
        raise RuntimeError(f"Invalid bot configuration: {exc}") from exc
