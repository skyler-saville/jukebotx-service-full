from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class RelaySettings(BaseSettings):
    bind_host: str = Field(default="0.0.0.0", alias="RELAY_BIND_HOST")
    port: int = Field(default=8090, gt=0, le=65535, alias="RELAY_PORT")
    public_base_url: str = Field(
        default="http://relay:8090",
        alias="RELAY_PUBLIC_BASE_URL",
    )
    control_token: str | None = Field(default=None, alias="AUDIO_RELAY_TOKEN")
    ffmpeg_path: str = Field(default="ffmpeg", alias="RELAY_FFMPEG_PATH")
    enable_browser_inputs: bool = Field(
        default=False,
        alias="RELAY_ENABLE_BROWSER_INPUTS",
    )
    chromium_path: str = Field(
        default="/usr/bin/chromium",
        alias="RELAY_CHROMIUM_PATH",
    )
    browser_profile_dir: Path = Field(
        default=Path("/data/chromium-profile"),
        alias="RELAY_BROWSER_PROFILE_DIR",
    )
    browser_navigation_timeout_seconds: float = Field(
        default=45.0,
        gt=0,
        alias="RELAY_BROWSER_NAVIGATION_TIMEOUT_SECONDS",
    )
    browser_playback_timeout_seconds: float = Field(
        default=45.0,
        gt=0,
        alias="RELAY_BROWSER_PLAYBACK_TIMEOUT_SECONDS",
    )
    pulse_server: str = Field(
        default="unix:/tmp/jukebotx-relay-runtime/pulse/native",
        alias="RELAY_PULSE_SERVER",
    )
    pulse_runtime_dir: Path = Field(
        default=Path("/tmp/jukebotx-relay-runtime"),
        alias="RELAY_PULSE_RUNTIME_DIR",
    )
    pulseaudio_path: str = Field(
        default="pulseaudio",
        alias="RELAY_PULSEAUDIO_PATH",
    )
    pactl_path: str = Field(default="pactl", alias="RELAY_PACTL_PATH")
    enable_synthetic_inputs: bool = Field(
        default=False,
        alias="RELAY_ENABLE_SYNTHETIC_INPUTS",
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        case_sensitive=False,
    )

    @field_validator("control_token", mode="before")
    @classmethod
    def _empty_token_to_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("public_base_url")
    @classmethod
    def _normalize_public_base_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        if not normalized.startswith(("http://", "https://")):
            raise ValueError("RELAY_PUBLIC_BASE_URL must be an absolute HTTP URL")
        return normalized
