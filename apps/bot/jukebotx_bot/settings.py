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

    jam_session_channel_id: int | None = Field(
        default=None, alias="JAM_SESSION_CHANNEL_ID"
    )
    jam_session_role_id: int | None = Field(default=None, alias="JAM_SESSION_ROLE_ID")
    web_base_url: str | None = Field(default=None, alias="WEB_BASE_URL")
    opus_api_base_url: str | None = Field(default=None, alias="OPUS_API_BASE_URL")
    voice_backend: str = Field(default="lavalink", alias="VOICE_BACKEND")

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
    def is_lavalink_backend(self) -> bool:
        return self.voice_backend.strip().lower() == "lavalink"

    @property
    def uses_lavalink(self) -> bool:
        return self.voice_backend.strip().lower() == "lavalink"

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

        if not self.uses_lavalink:
            return

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

    @field_validator("lavalink_session_id", "lavalink_resume_timeout_seconds", mode="before")
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
