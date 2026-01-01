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
