from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.extend(
    [
        str(ROOT / "apps" / "bot"),
        str(ROOT / "packages" / "core"),
        str(ROOT / "packages" / "infra"),
    ]
)

from jukebotx_bot.discord.now_playing import build_now_playing_embed
from jukebotx_bot.discord.session import Track


def test_build_now_playing_embed_includes_progress_bar_when_timing_is_known() -> None:
    track = Track(
        audio_url="https://cdn.example.com/test.mp3",
        opus_url=None,
        page_url="https://suno.com/song/example",
        title="Test Song",
        artist_display="Test Artist",
        media_url=None,
        requester_id=1,
        requester_name="tester",
        duration_seconds=200,
    )

    embed = build_now_playing_embed(track, started_at=100.0, now=150.0)

    progress_field = next(field for field in embed.fields if field.name.endswith("Progress"))
    assert progress_field.value == "[====------------] 0:50 / 3:20"
