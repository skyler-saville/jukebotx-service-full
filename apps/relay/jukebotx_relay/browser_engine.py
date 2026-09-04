from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from contextlib import suppress
from dataclasses import dataclass
import logging
import os
from pathlib import Path
from uuid import uuid4

from playwright.async_api import (
    BrowserContext,
    Page,
    Route,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

from jukebotx_relay.engine import RelayEngine, RelaySourceError


logger = logging.getLogger(__name__)


class BrowserCaptureError(RuntimeError):
    """Raised when the browser cannot produce a playable audio stream."""


@dataclass(frozen=True)
class PulseSink:
    name: str
    module_id: str


class PulseAudioServer:
    """Owns the private PulseAudio daemon and per-playback null sinks."""

    def __init__(
        self,
        *,
        server: str,
        runtime_dir: Path,
        pulseaudio_path: str = "pulseaudio",
        pactl_path: str = "pactl",
    ) -> None:
        self.server = server
        self._runtime_dir = runtime_dir
        self._pulseaudio_path = pulseaudio_path
        self._pactl_path = pactl_path
        self._start_lock = asyncio.Lock()

    @property
    def environment(self) -> dict[str, str]:
        return {
            "PULSE_SERVER": self.server,
            "XDG_RUNTIME_DIR": str(self._runtime_dir),
        }

    async def create_sink(self) -> PulseSink:
        await self.ensure_started()
        name = f"relay_{uuid4().hex}"
        module_id = (
            await self._run(
                self._pactl_path,
                "load-module",
                "module-null-sink",
                f"sink_name={name}",
                "rate=48000",
                "channels=2",
                "sink_properties=device.description=JukeBotx_Relay",
            )
        ).strip()
        if not module_id.isdigit():
            raise BrowserCaptureError(
                f"PulseAudio returned an invalid module id: {module_id!r}"
            )
        return PulseSink(name=name, module_id=module_id)

    async def remove_sink(self, sink: PulseSink) -> None:
        with suppress(BrowserCaptureError):
            await self._run(self._pactl_path, "unload-module", sink.module_id)

    async def ensure_started(self) -> None:
        async with self._start_lock:
            if await self._is_ready():
                await self._disable_idle_suspension()
                return

            self._runtime_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            self._runtime_dir.chmod(0o700)
            await self._run(
                self._pulseaudio_path,
                "--daemonize=yes",
                "--exit-idle-time=-1",
                "--log-target=stderr",
                environment=self.environment,
            )

            for _ in range(50):
                if await self._is_ready():
                    await self._disable_idle_suspension()
                    return
                await asyncio.sleep(0.1)
            raise BrowserCaptureError("PulseAudio did not become ready")

    async def _disable_idle_suspension(self) -> None:
        # A suspended null sink does not feed silence to its monitor. Keeping it
        # active lets FFmpeg emit an Ogg header immediately while Chromium loads.
        with suppress(BrowserCaptureError):
            await self._run(
                self._pactl_path,
                "unload-module",
                "module-suspend-on-idle",
            )

    async def _is_ready(self) -> bool:
        process = await asyncio.create_subprocess_exec(
            self._pactl_path,
            "info",
            env={**os.environ, **self.environment},
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        return await process.wait() == 0

    async def _run(
        self,
        *command: str,
        environment: Mapping[str, str] | None = None,
    ) -> str:
        process = await asyncio.create_subprocess_exec(
            *command,
            env={**os.environ, **(environment or self.environment)},
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            detail = stderr.decode(errors="replace").strip()
            raise BrowserCaptureError(
                f"Command {command[0]!r} failed with code {process.returncode}: {detail}"
            )
        return stdout.decode(errors="replace")


class SunoBrowserAudioEngine(RelayEngine):
    """Streams audio that Chromium is already authorized to play on a Suno page."""

    name = "suno-browser"
    content_type = "audio/ogg"

    def __init__(
        self,
        *,
        ffmpeg_path: str,
        chromium_path: str,
        profile_dir: Path,
        pulse_server: PulseAudioServer,
        navigation_timeout_seconds: float = 45.0,
        playback_timeout_seconds: float = 20.0,
    ) -> None:
        self._ffmpeg_path = ffmpeg_path
        self._chromium_path = chromium_path
        self._profile_dir = profile_dir
        self._pulse_server = pulse_server
        self._navigation_timeout_ms = navigation_timeout_seconds * 1000
        self._playback_timeout_ms = playback_timeout_seconds * 1000
        # Chromium locks a persistent user-data directory. A listening party only
        # needs one browser capture at a time, so serialize profile access.
        self._profile_lock = asyncio.Lock()

    def supports(self, source_url: str) -> bool:
        from urllib.parse import urlparse

        parsed = urlparse(source_url)
        return parsed.scheme == "https" and (parsed.hostname or "").lower() in {
            "suno.com",
            "www.suno.com",
        }

    def validate_source(self, source_url: str) -> None:
        from urllib.parse import urlparse

        parsed = urlparse(source_url)
        if not self.supports(source_url):
            raise RelaySourceError("Expected an HTTPS Suno URL")
        if not parsed.path.startswith(("/song/", "/s/")):
            raise RelaySourceError("Expected a Suno song or share URL")

    async def stream(
        self,
        source_url: str,
        *,
        stop_event: asyncio.Event,
    ) -> AsyncIterator[bytes]:
        self.validate_source(source_url)
        async with self._profile_lock:
            async for chunk in self._stream_locked(
                source_url,
                stop_event=stop_event,
            ):
                yield chunk

    async def _stream_locked(
        self,
        source_url: str,
        *,
        stop_event: asyncio.Event,
    ) -> AsyncIterator[bytes]:
        sink = await self._pulse_server.create_sink()
        context: BrowserContext | None = None
        silence: asyncio.subprocess.Process | None = None
        ffmpeg: asyncio.subprocess.Process | None = None
        self._profile_dir.mkdir(parents=True, exist_ok=True)
        self._clear_stale_profile_locks()

        try:
            silence = await self._start_silence(sink)
            browser_environment: dict[str, str | float | bool] = {
                **os.environ,
                **self._pulse_server.environment,
                "PULSE_SINK": sink.name,
            }
            async with async_playwright() as playwright:
                context = await playwright.chromium.launch_persistent_context(
                    user_data_dir=str(self._profile_dir),
                    executable_path=self._chromium_path,
                    headless=False,
                    env=browser_environment,
                    ignore_default_args=["--mute-audio", "--disable-dev-shm-usage"],
                    args=[
                        "--autoplay-policy=no-user-gesture-required",
                        "--no-sandbox",
                    ],
                )
                await context.route("**/*", self._route_lightweight_page)
                page = context.pages[0] if context.pages else await context.new_page()
                await page.goto(
                    source_url,
                    wait_until="domcontentloaded",
                    timeout=self._navigation_timeout_ms,
                )

                ffmpeg = await self._start_ffmpeg(sink)
                ended_task = asyncio.create_task(self._play_until_end(page))
                stop_task = asyncio.create_task(stop_event.wait())
                try:
                    assert ffmpeg.stdout is not None
                    while True:
                        read_task = asyncio.create_task(ffmpeg.stdout.read(64 * 1024))
                        done, _ = await asyncio.wait(
                            {read_task, ended_task, stop_task},
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        if read_task in done:
                            chunk = read_task.result()
                            if not chunk:
                                break
                            yield chunk
                            continue

                        read_task.cancel()
                        await asyncio.gather(read_task, return_exceptions=True)
                        if ended_task in done:
                            ended_task.result()
                        break

                    if not stop_event.is_set() and ffmpeg.returncode is None:
                        await self._request_ffmpeg_stop(ffmpeg)
                        while True:
                            chunk = await ffmpeg.stdout.read(64 * 1024)
                            if not chunk:
                                break
                            yield chunk
                        return_code = await ffmpeg.wait()
                        if return_code != 0:
                            raise await self._ffmpeg_error(ffmpeg, return_code)
                finally:
                    ended_task.cancel()
                    stop_task.cancel()
                    await asyncio.gather(
                        ended_task,
                        stop_task,
                        return_exceptions=True,
                    )
        finally:
            if ffmpeg is not None:
                await self._terminate_process(ffmpeg)
            if silence is not None:
                await self._terminate_process(silence)
            if context is not None:
                with suppress(Exception):
                    await context.close()
            await self._pulse_server.remove_sink(sink)

    async def _start_silence(self, sink: PulseSink) -> asyncio.subprocess.Process:
        """Keep the null sink clocked while Chromium is loading the source page."""
        process = await asyncio.create_subprocess_exec(
            self._ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-f",
            "pulse",
            sink.name,
            env={
                **os.environ,
                **self._pulse_server.environment,
                "PULSE_SINK": sink.name,
            },
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.sleep(0.1)
        if process.returncode is not None:
            raise await self._ffmpeg_error(process, process.returncode)
        return process

    async def _start_ffmpeg(self, sink: PulseSink) -> asyncio.subprocess.Process:
        process = await asyncio.create_subprocess_exec(
            self._ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "pulse",
            "-i",
            f"{sink.name}.monitor",
            "-vn",
            "-ac",
            "2",
            "-ar",
            "48000",
            "-c:a",
            "libopus",
            "-application",
            "audio",
            "-b:a",
            "128k",
            "-vbr",
            "off",
            "-flush_packets",
            "1",
            "-f",
            "ogg",
            "pipe:1",
            env={**os.environ, **self._pulse_server.environment},
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.sleep(0.1)
        if process.returncode is not None:
            raise await self._ffmpeg_error(process, process.returncode)
        return process

    async def _start_suno_playback(self, page: Page) -> None:
        if await self._playback_is_active(page):
            return

        candidates = [
            page.get_by_role("button", name="Play", exact=True),
            page.get_by_role("button", name="Playbar: Play button", exact=True),
        ]
        clicked = False
        for candidate in candidates:
            try:
                await candidate.first.wait_for(
                    state="visible",
                    timeout=self._playback_timeout_ms,
                )
            except PlaywrightTimeoutError:
                continue
            for index in range(await candidate.count()):
                button = candidate.nth(index)
                if await button.is_visible():
                    # Suno currently leaves Playwright waiting on an internal
                    # navigation after the trusted click is dispatched. Treat a
                    # short click timeout as dispatched and verify media state.
                    try:
                        await button.click(timeout=2_000, no_wait_after=True)
                    except PlaywrightTimeoutError:
                        pass
                    clicked = True
                    break
            if clicked:
                break

        if not clicked:
            logger.error(
                "Suno play control was not visible after navigation: title=%r url=%r",
                await page.title(),
                page.url,
            )
            raise BrowserCaptureError("Suno did not expose a visible play button")

        try:
            await page.wait_for_function(
                """
                () => Array.from(document.querySelectorAll('audio, video')).some(
                    (media) => !media.paused && !media.ended && media.readyState >= 2
                )
                """,
                timeout=self._playback_timeout_ms,
            )
        except Exception as exc:
            raise BrowserCaptureError(
                "Suno playback did not start before the relay timeout"
            ) from exc

    async def _play_until_end(self, page: Page) -> None:
        await self._start_suno_playback(page)
        await self._wait_for_playback_end(page)

    async def _wait_for_playback_end(self, page: Page) -> None:
        missing_since: float | None = None
        loop = asyncio.get_running_loop()
        while True:
            state = await page.evaluate(
                """
                () => {
                    const media = Array.from(document.querySelectorAll('audio, video'))
                        .find((item) => item.duration > 1);
                    if (!media) return { present: false };
                    return {
                        present: true,
                        ended: media.ended,
                        paused: media.paused,
                        currentTime: media.currentTime,
                        duration: media.duration,
                    };
                }
                """
            )
            if state.get("present"):
                missing_since = None
                duration = float(state.get("duration") or 0)
                current_time = float(state.get("currentTime") or 0)
                if state.get("ended") or (
                    duration > 1 and current_time >= duration - 0.25
                ):
                    return
                if state.get("paused") and current_time > 1:
                    return
            else:
                missing_since = missing_since or loop.time()
                if loop.time() - missing_since > 10:
                    raise BrowserCaptureError(
                        "Suno removed its playable media element during relay"
                    )
            await asyncio.sleep(0.5)

    @staticmethod
    def _remove_path(path: Path) -> None:
        with suppress(FileNotFoundError):
            path.unlink()

    def _clear_stale_profile_locks(self) -> None:
        # These point at a previous container hostname after an unclean stop.
        # The engine's profile mutex guarantees no live browser owns them here.
        for name in ("SingletonCookie", "SingletonLock", "SingletonSocket"):
            self._remove_path(self._profile_dir / name)

    @staticmethod
    async def _route_lightweight_page(route: Route) -> None:
        if route.request.resource_type in {"font", "image"}:
            await route.abort()
            return
        await route.continue_()

    @staticmethod
    async def _playback_is_active(page: Page) -> bool:
        return bool(
            await page.evaluate(
                """
                () => Array.from(document.querySelectorAll('audio, video')).some(
                    (media) => !media.paused && !media.ended && media.readyState >= 2
                )
                """
            )
        )

    @staticmethod
    async def _request_ffmpeg_stop(process: asyncio.subprocess.Process) -> None:
        if process.stdin is None or process.returncode is not None:
            return
        process.stdin.write(b"q\n")
        with suppress(BrokenPipeError, ConnectionResetError):
            await process.stdin.drain()

    @staticmethod
    async def _terminate_process(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=3.0)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()

    @staticmethod
    async def _ffmpeg_error(
        process: asyncio.subprocess.Process,
        return_code: int,
    ) -> BrowserCaptureError:
        stderr = b""
        if process.stderr is not None:
            stderr = await process.stderr.read()
        detail = stderr.decode(errors="replace").strip()
        return BrowserCaptureError(
            f"FFmpeg browser capture failed with code {return_code}: {detail}"
        )
