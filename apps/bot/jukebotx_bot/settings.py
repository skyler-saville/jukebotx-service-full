# apps/bot/jukebotx_bot/settings.py
from __future__ import annotations

from pydantic import Field, ValidationError, field_validator
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
    discord_guild_id: int | None = Field(default=None, alias="DISCORD_GUILD_ID")
    master_user_id: int | None = Field(default=None, alias="MASTER_USER_ID")

    jam_session_channel_id: int | None = Field(
        default=None, alias="JAM_SESSION_CHANNEL_ID"
    )
    jam_session_role_id: int | None = Field(default=None, alias="JAM_SESSION_ROLE_ID")
    web_base_url: str | None = Field(default=None, alias="WEB_BASE_URL")
    public_api_base_url: str | None = Field(default=None, alias="PUBLIC_API_BASE_URL")
    api_session_secret: str | None = Field(default=None, alias="API_SESSION_SECRET")
    opus_api_base_url: str | None = Field(default=None, alias="OPUS_API_BASE_URL")
    playlist_download_link_ttl_seconds: int = Field(
        default=86400,
        alias="PLAYLIST_DOWNLOAD_LINK_TTL_SECONDS",
    )
    playlist_archive_storage_prefix: str = Field(
        default="downloads/playlists",
        alias="PLAYLIST_ARCHIVE_STORAGE_PREFIX",
    )
    voice_backend: str = Field(default="lavalink", alias="VOICE_BACKEND")
    audio_relay_base_url: str | None = Field(default=None, alias="AUDIO_RELAY_BASE_URL")
    audio_relay_token: str | None = Field(default=None, alias="AUDIO_RELAY_TOKEN")
    audio_relay_timeout_seconds: float = Field(
        default=30.0,
        gt=0,
        alias="AUDIO_RELAY_TIMEOUT_SECONDS",
    )

    opus_storage_provider: str = Field(default="s3", alias="OPUS_STORAGE_PROVIDER")
    opus_storage_bucket: str | None = Field(default=None, alias="OPUS_STORAGE_BUCKET")
    opus_storage_prefix: str = Field(default="opus", alias="OPUS_STORAGE_PREFIX")
    opus_storage_region: str | None = Field(default=None, alias="OPUS_STORAGE_REGION")
    opus_storage_endpoint_url: str | None = Field(default=None, alias="OPUS_STORAGE_ENDPOINT_URL")
    opus_storage_access_key_id: str | None = Field(default=None, alias="OPUS_STORAGE_ACCESS_KEY_ID")
    opus_storage_secret_access_key: str | None = Field(default=None, alias="OPUS_STORAGE_SECRET_ACCESS_KEY")
    opus_storage_public_base_url: str | None = Field(default=None, alias="OPUS_STORAGE_PUBLIC_BASE_URL")
    opus_storage_signed_url_ttl_seconds: int = Field(
        default=900,
        alias="OPUS_STORAGE_SIGNED_URL_TTL_SECONDS",
    )
    opus_storage_ttl_seconds: int = Field(default=604800, alias="OPUS_STORAGE_TTL_SECONDS")

    lavalink_host: str | None = Field(default=None, alias="LAVALINK_HOST")
    lavalink_port: int = Field(default=2333, alias="LAVALINK_PORT")
    lavalink_password: str | None = Field(default=None, alias="LAVALINK_PASSWORD")
    lavalink_secure: bool = Field(default=False, alias="LAVALINK_SECURE")
    lavalink_session_id: str | None = Field(default=None, alias="LAVALINK_SESSION_ID")
    lavalink_resume_timeout_seconds: int | None = Field(
        default=None,
        alias="LAVALINK_RESUME_TIMEOUT_SECONDS",
    )

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

    @property
    def lavalink_uri(self) -> str:
        scheme = "https" if self.lavalink_secure else "http"
        return f"{scheme}://{self.lavalink_host}:{self.lavalink_port}"

    def validate_startup(self) -> None:
        backend = self.voice_backend.strip().lower()
        if backend != "lavalink":
            raise RuntimeError(
                "Lavalink-only mode is enabled. VOICE_BACKEND must be set to 'lavalink'."
            )

        missing_vars: list[str] = []
        if not self.lavalink_host:
            missing_vars.append("LAVALINK_HOST")
        if not self.lavalink_password:
            missing_vars.append("LAVALINK_PASSWORD")

        if missing_vars:
            joined = ", ".join(missing_vars)
            raise RuntimeError(
                "VOICE_BACKEND=lavalink requires the following environment variables: "
                f"{joined}."
            )

    @field_validator(
        "web_base_url",
        "public_api_base_url",
        "api_session_secret",
        "opus_api_base_url",
        "opus_storage_bucket",
        "opus_storage_region",
        "opus_storage_endpoint_url",
        "opus_storage_access_key_id",
        "opus_storage_secret_access_key",
        "opus_storage_public_base_url",
        "lavalink_session_id",
        "lavalink_resume_timeout_seconds",
        "audio_relay_base_url",
        "audio_relay_token",
        mode="before",
    )
    @classmethod
    def _empty_lavalink_optional_values_to_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value


def load_bot_settings() -> BotSettings:
    """
    Load and validate bot settings. Raises a RuntimeError with a readable message on failure.
    """
    try:
        return BotSettings()
    except ValidationError as exc:
        raise RuntimeError(f"Invalid bot configuration: {exc}") from exc
