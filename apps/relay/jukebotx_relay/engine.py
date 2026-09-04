from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse


class RelaySourceError(ValueError):
    """Raised when a relay source is unsupported or invalid."""


class RelayEngine:
    """Extension point for a source-specific audio capture engine."""

    name = "unknown"
    content_type = "application/octet-stream"

    def supports(self, source_url: str) -> bool:
        raise NotImplementedError

    def validate_source(self, source_url: str) -> None:
        raise NotImplementedError

    def stream(
        self,
        source_url: str,
        *,
        stop_event: asyncio.Event,
    ) -> AsyncIterator[bytes]:
        raise NotImplementedError


@dataclass(frozen=True)
class SyntheticTone:
    frequency_hz: float
    duration_seconds: float


class SyntheticToneEngine(RelayEngine):
    """FFmpeg tone source used only to verify relay plumbing."""

    name = "synthetic-tone"
    content_type = "audio/ogg"

    def __init__(self, *, ffmpeg_path: str = "ffmpeg") -> None:
        self._ffmpeg_path = ffmpeg_path

    def supports(self, source_url: str) -> bool:
        parsed = urlparse(source_url)
        return parsed.scheme == "synthetic" and parsed.netloc == "tone"

    def validate_source(self, source_url: str) -> None:
        self._parse_source(source_url)

    async def stream(
        self,
        source_url: str,
        *,
        stop_event: asyncio.Event,
    ) -> AsyncIterator[bytes]:
        tone = self._parse_source(source_url)
        source = (
            f"sine=frequency={tone.frequency_hz:g}:"
            f"sample_rate=48000:duration={tone.duration_seconds:g}"
        )
        process = await asyncio.create_subprocess_exec(
            self._ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            source,
            "-vn",
            "-c:a",
            "libopus",
            "-b:a",
            "128k",
            "-f",
            "ogg",
            "pipe:1",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert process.stdout is not None
        assert process.stderr is not None

        stop_task = asyncio.create_task(stop_event.wait())
        try:
            while True:
                read_task = asyncio.create_task(process.stdout.read(64 * 1024))
                done, _ = await asyncio.wait(
                    {read_task, stop_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if stop_task in done:
                    read_task.cancel()
                    await asyncio.gather(read_task, return_exceptions=True)
                    break

                chunk = read_task.result()
                if not chunk:
                    break
                yield chunk

            if not stop_event.is_set():
                return_code = await process.wait()
                if return_code != 0:
                    stderr = (
                        (await process.stderr.read()).decode(errors="replace").strip()
                    )
                    raise RuntimeError(f"FFmpeg relay stream failed: {stderr}")
        finally:
            stop_task.cancel()
            await asyncio.gather(stop_task, return_exceptions=True)
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=3.0)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()

    @staticmethod
    def _parse_source(source_url: str) -> SyntheticTone:
        parsed = urlparse(source_url)
        if parsed.scheme != "synthetic" or parsed.netloc != "tone":
            raise RelaySourceError("Expected a synthetic://tone source URL")

        query = parse_qs(parsed.query)
        try:
            frequency_hz = float(query.get("frequency", ["440"])[0])
            duration_seconds = float(query.get("duration", ["2"])[0])
        except (TypeError, ValueError) as exc:
            raise RelaySourceError("Synthetic tone parameters must be numbers") from exc

        if not 20 <= frequency_hz <= 20_000:
            raise RelaySourceError(
                "Synthetic tone frequency must be between 20 and 20000 Hz"
            )
        if not 0.1 <= duration_seconds <= 600:
            raise RelaySourceError(
                "Synthetic tone duration must be between 0.1 and 600 seconds"
            )

        return SyntheticTone(
            frequency_hz=frequency_hz,
            duration_seconds=duration_seconds,
        )
