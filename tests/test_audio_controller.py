from pathlib import Path
import sys
import asyncio

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.extend(
    [
        str(ROOT / "apps" / "bot"),
        str(ROOT / "packages" / "core"),
        str(ROOT / "packages" / "infra"),
    ]
)

from jukebotx_bot.discord.audio import AudioControllerManager, GuildAudioController
from jukebotx_bot.discord.session import SessionState, Track
from jukebotx_bot.voice.backends.base import PlaybackBackend, TrackEndHook
from jukebotx_bot.voice.backends.lavalink import LavalinkPlaybackBackend


class FakeVoiceClient:
    def __init__(self) -> None:
        self.playing = False
        self.guild = None


class FakePlaybackBackend(PlaybackBackend):
    def __init__(self) -> None:
        self.play_calls: list[tuple[FakeVoiceClient, str]] = []
        self.stop_calls = 0
        self._hooks: list[TrackEndHook] = []
        self._next_source = 0

    async def connect(self, channel):
        raise NotImplementedError

    async def disconnect(self, voice_client):
        raise NotImplementedError

    async def play_track(self, voice_client, url: str) -> object:
        voice_client.playing = True
        self._next_source += 1
        source = f"source-{self._next_source}"
        self.play_calls.append((voice_client, url))
        return source

    async def stop(self, voice_client) -> None:
        voice_client.playing = False
        self.stop_calls += 1

    async def skip(self, voice_client) -> None:
        await self.stop(voice_client)

    def is_playing(self, voice_client) -> bool:
        return voice_client.playing

    def add_track_end_hook(self, hook: TrackEndHook) -> None:
        self._hooks.append(hook)

    def prefer_source_audio_url(self) -> bool:
        return False

    async def emit_track_end(self, voice_client, source: object, error: Exception | None = None) -> None:
        for hook in self._hooks:
            result = hook(voice_client, source, error)
            if result is not None:
                await result


class FakePreferAudioUrlBackend(FakePlaybackBackend):
    def prefer_source_audio_url(self) -> bool:
        return True


class FakeRuntimeErrorBackend(FakePlaybackBackend):
    async def play_track(self, voice_client, url: str) -> object:
        raise RuntimeError("voice not connected")


class FakeValueErrorBackend(FakePlaybackBackend):
    async def play_track(self, voice_client, url: str) -> object:
        raise ValueError("invalid url")


def _build_track(title: str, requester_id: int, requester_name: str) -> Track:
    return Track(
        audio_url=f"https://example.com/{title}.mp3",
        opus_url=None,
        page_url=f"https://example.com/song/{title}",
        title=title,
        artist_display="Test Artist",
        media_url=f"https://example.com/media/{title}",
        requester_id=requester_id,
        requester_name=requester_name,
    )


@pytest.mark.asyncio
async def test_play_next_starts_track() -> None:
    session = SessionState()
    session.queue.append(_build_track("track1", 1, "User"))
    backend = FakePlaybackBackend()
    controller = GuildAudioController(guild_id=123, session=session, backend=backend)
    voice_client = FakeVoiceClient()

    started = await controller.play_next(voice_client)

    assert started is not None
    assert started.title == "track1"
    assert voice_client.playing
    assert len(backend.play_calls) == 1


@pytest.mark.asyncio
async def test_play_next_runtime_error_rolls_back_queue_state() -> None:
    session = SessionState()
    session.queue.append(_build_track("track1", 1, "User"))
    backend = FakeRuntimeErrorBackend()
    controller = GuildAudioController(guild_id=123, session=session, backend=backend)
    voice_client = FakeVoiceClient()

    with pytest.raises(RuntimeError):
        await controller.play_next(voice_client)

    assert session.now_playing is None
    assert [track.title for track in session.queue] == ["track1"]


@pytest.mark.asyncio
async def test_play_next_value_error_rolls_back_queue_state() -> None:
    session = SessionState()
    session.queue.append(_build_track("track1", 1, "User"))
    backend = FakeValueErrorBackend()
    controller = GuildAudioController(guild_id=123, session=session, backend=backend)
    voice_client = FakeVoiceClient()

    started = await controller.play_next(voice_client)

    assert started is None
    assert session.now_playing is None
    assert [track.title for track in session.queue] == ["track1"]


@pytest.mark.asyncio
async def test_stop_uses_backend_and_resets_session() -> None:
    session = SessionState()
    session.queue.append(_build_track("track1", 1, "User"))
    backend = FakePlaybackBackend()
    controller = GuildAudioController(guild_id=123, session=session, backend=backend)
    voice_client = FakeVoiceClient()

    await controller.play_next(voice_client)
    source = controller._current_source
    assert source is not None

    await controller.stop(voice_client)

    assert backend.stop_calls == 1
    assert controller._current_source is None
    assert session.now_playing is None


@pytest.mark.asyncio
async def test_track_end_without_autoplay_stops_playback_and_keeps_queue() -> None:
    session = SessionState()
    session.queue.append(_build_track("track1", 1, "User"))
    session.queue.append(_build_track("track2", 2, "User2"))
    backend = FakePlaybackBackend()
    controller = GuildAudioController(guild_id=123, session=session, backend=backend)
    voice_client = FakeVoiceClient()

    first = await controller.play_next(voice_client)
    assert first is not None
    current_source = controller._current_source
    assert current_source is not None

    voice_client.playing = False
    await backend.emit_track_end(voice_client, current_source)

    assert session.now_playing is None
    assert [track.title for track in session.queue] == ["track2"]
    assert len(backend.play_calls) == 1


@pytest.mark.asyncio
async def test_track_end_autoplay_advances_and_turns_off_when_counter_reaches_zero() -> None:
    session = SessionState()
    session.set_autoplay(2)
    session.queue.append(_build_track("track1", 1, "User"))
    session.queue.append(_build_track("track2", 2, "User2"))
    backend = FakePlaybackBackend()
    controller = GuildAudioController(guild_id=123, session=session, backend=backend)
    voice_client = FakeVoiceClient()

    first = await controller.play_next(voice_client)
    assert first is not None
    assert session.autoplay_enabled is True
    current_source = controller._current_source
    assert current_source is not None

    voice_client.playing = False
    await backend.emit_track_end(voice_client, current_source)

    assert session.now_playing is not None
    assert session.now_playing.title == "track2"
    assert session.autoplay_enabled is False
    assert session.autoplay_remaining is None
    assert len(backend.play_calls) == 2


def test_session_state_start_next_track_applies_autoplay_transitions_without_backend() -> None:
    session = SessionState()
    session.set_autoplay(1)
    session.queue.append(_build_track("track1", 1, "User"))

    track = session.start_next_track()

    assert track is not None
    assert track.title == "track1"
    assert session.now_playing is track
    assert session.autoplay_enabled is False
    assert session.autoplay_remaining is None


@pytest.mark.asyncio
async def test_resolve_playback_url_prefers_audio_url_when_backend_requests_it() -> None:
    session = SessionState()
    backend = FakePreferAudioUrlBackend()
    controller = GuildAudioController(guild_id=321, session=session, backend=backend)
    track = Track(
        audio_url="https://example.com/source.mp3",
        opus_url="https://example.com/cached.opus",
        page_url="https://example.com/song/source",
        title="source",
        artist_display="Test Artist",
        media_url="https://example.com/media/source",
        requester_id=1,
        requester_name="User",
    )

    resolved = await controller._resolve_playback_url(track)
    assert resolved == "https://example.com/source.mp3"


@pytest.mark.asyncio
async def test_play_next_skips_opus_wait_when_backend_prefers_source_audio(monkeypatch: pytest.MonkeyPatch) -> None:
    session = SessionState()
    track = _build_track("track1", 1, "User")
    track.opus_url = "https://example.com/cached.opus"
    session.queue.append(track)
    backend = FakePreferAudioUrlBackend()
    controller = GuildAudioController(guild_id=777, session=session, backend=backend)
    voice_client = FakeVoiceClient()

    async def fail_wait(_: Track) -> None:
        raise AssertionError("Opus wait should not run for source-audio playback")

    monkeypatch.setattr(controller, "_wait_for_opus_ready", fail_wait)
    monkeypatch.setattr(controller, "_probe_duration_seconds", lambda url: asyncio.sleep(0, result=123.0))

    started = await controller.play_next(voice_client)

    assert started is track
    assert backend.play_calls == [(voice_client, track.audio_url)]


@pytest.mark.asyncio
async def test_play_next_starts_before_duration_probe_finishes(monkeypatch: pytest.MonkeyPatch) -> None:
    session = SessionState()
    track = _build_track("track1", 1, "User")
    session.queue.append(track)
    backend = FakePreferAudioUrlBackend()
    controller = GuildAudioController(guild_id=888, session=session, backend=backend)
    voice_client = FakeVoiceClient()
    probe_started = asyncio.Event()
    allow_probe_finish = asyncio.Event()

    async def slow_probe(_: str) -> float:
        probe_started.set()
        await allow_probe_finish.wait()
        return 321.0

    monkeypatch.setattr(controller, "_probe_duration_seconds", slow_probe)

    started = await controller.play_next(voice_client)

    assert started is track
    assert backend.play_calls == [(voice_client, track.audio_url)]
    await asyncio.wait_for(probe_started.wait(), timeout=1.0)
    assert track.duration_seconds is None

    allow_probe_finish.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert track.duration_seconds == 321.0


def test_audio_controller_manager_supports_lavalink_backend_mode() -> None:
    manager = AudioControllerManager(backend_name="lavalink")
    controller = manager.for_guild(999, SessionState())
    assert isinstance(controller._backend, LavalinkPlaybackBackend)
