from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
sys.path.extend(
    [
        str(ROOT / "apps" / "bot"),
        str(ROOT / "packages" / "core"),
        str(ROOT / "packages" / "infra"),
    ]
)


class _EmbedImage:
    def __init__(self) -> None:
        self.url = None


class _Embed:
    def __init__(self, *, title: str, description: str, color: int) -> None:
        self.title = title
        self.description = description
        self.color = color
        self.fields: list[dict[str, object]] = []
        self.image = _EmbedImage()

    def add_field(self, *, name: str, value: str, inline: bool) -> None:
        self.fields.append({"name": name, "value": value, "inline": inline})

    def set_image(self, *, url: str) -> None:
        self.image.url = url


sys.modules.setdefault("discord", types.SimpleNamespace(Embed=_Embed))

from jukebotx_bot.discord.now_playing import build_now_playing_embed
from jukebotx_bot.discord.session import Track


def _track(media_url: str | None) -> Track:
    return Track(
        audio_url="https://audio.example.com/track.mp3",
        opus_url=None,
        page_url="https://suno.example.com/song",
        title="Track",
        artist_display="Artist",
        media_url=media_url,
        requester_id=42,
        requester_name="DJ",
    )


def test_build_now_playing_embed_keeps_image_url() -> None:
    embed = build_now_playing_embed(_track("https://cdn.example.com/cover.webp"))

    assert embed.image.url == "https://cdn.example.com/cover.webp"


def test_build_now_playing_embed_converts_mp4_url_to_gif() -> None:
    embed = build_now_playing_embed(_track("https://cdn.example.com/video.mp4?token=abc"))

    assert embed.image.url == "https://cdn.example.com/video.gif?token=abc"


def test_build_now_playing_embed_with_missing_image_url_stays_valid() -> None:
    embed = build_now_playing_embed(_track(None))

    assert embed.title == "Track"
    assert embed.image.url is None
