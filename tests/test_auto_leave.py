from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from types import SimpleNamespace

import discord

ROOT = Path(__file__).resolve().parents[1]
sys.path.extend(
    [
        str(ROOT / "apps" / "bot"),
        str(ROOT / "packages" / "core"),
        str(ROOT / "packages" / "infra"),
    ]
)

from jukebotx_bot.discord.session import SessionManager, SessionState, Track
from jukebotx_bot.main import AUTO_LEAVE_IDLE_SECONDS, AUTO_LEAVE_SOLO_SECONDS, BotDeps, JukeBot, StreamRecord


class FakeVoiceClient:
    def __init__(self, members):
        self.channel = SimpleNamespace(id=10, members=members)


def _build_bot() -> JukeBot:
    deps = BotDeps(
        session_manager=SessionManager(),
        ingest_use_case=SimpleNamespace(),
        audio_manager=SimpleNamespace(),
        playlist_client=SimpleNamespace(),
        submission_repo=SimpleNamespace(),
        queue_repo=SimpleNamespace(),
    )
    settings = SimpleNamespace(env="development", opus_api_base_url=None)
    return JukeBot(
        settings=settings,
        deps=deps,
        command_prefix=";",
        intents=discord.Intents.none(),
    )


def test_stop_playback_updates_last_playback_event_timestamp() -> None:
    session = SessionState()
    session.now_playing = Track(
        audio_url="https://cdn.suno.ai/track.mp3",
        opus_url=None,
        page_url=None,
        title="Example",
        artist_display="Artist",
        media_url=None,
        requester_id=1,
        requester_name="DJ",
    )
    before = session.last_playback_event_at

    session.stop_playback()

    assert session.now_playing is None
    assert session.last_playback_event_at >= before


def test_should_auto_leave_when_bot_is_alone_in_open_session() -> None:
    bot = _build_bot()
    session = SessionState(submissions_open=True)
    stream = StreamRecord(
        guild_id=1,
        voice_channel_id=10,
        owner_user_id=123,
        created_at=datetime.now(timezone.utc) - timedelta(seconds=AUTO_LEAVE_SOLO_SECONDS + 5),
    )
    members = [SimpleNamespace(bot=True)]
    voice_client = FakeVoiceClient(members)

    reason = bot._should_auto_leave(
        session=session,
        stream=stream,
        voice_client=voice_client,
        now_epoch=datetime.now(timezone.utc).timestamp(),
        now_monotonic=session.last_playback_event_at,
    )

    assert reason == "bot alone in voice channel"


def test_should_auto_leave_when_queue_is_idle_for_timeout() -> None:
    bot = _build_bot()
    session = SessionState(submissions_open=False)
    session.last_playback_event_at -= AUTO_LEAVE_IDLE_SECONDS + 5
    stream = StreamRecord(
        guild_id=1,
        voice_channel_id=10,
        owner_user_id=123,
        created_at=datetime.now(timezone.utc),
    )
    members = [SimpleNamespace(bot=True), SimpleNamespace(bot=False)]
    voice_client = FakeVoiceClient(members)

    reason = bot._should_auto_leave(
        session=session,
        stream=stream,
        voice_client=voice_client,
        now_epoch=datetime.now(timezone.utc).timestamp(),
        now_monotonic=session.last_playback_event_at + AUTO_LEAVE_IDLE_SECONDS + 6,
    )

    assert reason == "queue empty and playback idle"
