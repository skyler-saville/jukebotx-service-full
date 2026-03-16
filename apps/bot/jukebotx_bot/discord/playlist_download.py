from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import tempfile
from urllib.parse import unquote, urlparse
import zipfile

import httpx


_INVALID_FILENAME_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1F]+')
_WHITESPACE_RE = re.compile(r"\s+")
_ZIP_ENTRY_OVERHEAD_BYTES = 256


@dataclass(frozen=True)
class PlaylistArchiveTrack:
    source_index: int
    title: str | None
    artist_display: str | None
    audio_url: str


@dataclass(frozen=True)
class PlaylistArchiveSkippedTrack:
    source_index: int
    title: str | None
    audio_url: str
    reason: str


@dataclass(frozen=True)
class PlaylistArchiveSummary:
    added_count: int
    skipped: tuple[PlaylistArchiveSkippedTrack, ...]

    @property
    def skipped_count(self) -> int:
        return len(self.skipped)


@dataclass(frozen=True)
class PlaylistArchivePart:
    local_path: Path
    added_count: int
    size_bytes: int


@dataclass(frozen=True)
class PlaylistArchiveBatchSummary:
    parts: tuple[PlaylistArchivePart, ...]
    added_count: int
    skipped: tuple[PlaylistArchiveSkippedTrack, ...]

    @property
    def part_count(self) -> int:
        return len(self.parts)

    @property
    def skipped_count(self) -> int:
        return len(self.skipped)


def _sanitize_filename_component(value: str | None, *, fallback: str) -> str:
    if value is None:
        return fallback

    sanitized = _INVALID_FILENAME_CHARS_RE.sub(" ", value)
    sanitized = _WHITESPACE_RE.sub(" ", sanitized).strip(" ._-")
    return sanitized[:120] or fallback


def _audio_extension_from_url(audio_url: str) -> str:
    path = unquote(urlparse(audio_url).path)
    suffix = Path(path).suffix.lower()
    if re.fullmatch(r"\.[a-z0-9]{1,5}", suffix):
        return suffix
    return ".mp3"


def build_playlist_archive_name(playlist_url: str) -> str:
    playlist_id = Path(urlparse(playlist_url).path).name
    safe_playlist_id = _sanitize_filename_component(playlist_id, fallback="playlist")
    return f"suno_playlist_{safe_playlist_id}.zip"


def build_playlist_archive_part_filename(base_name: str, *, part_index: int, part_count: int) -> str:
    if part_count <= 1:
        return base_name

    base_path = Path(base_name)
    width = max(2, len(str(part_count)))
    return f"{base_path.stem}_part{part_index:0{width}}of{part_count:0{width}}{base_path.suffix}"


def build_playlist_track_filename(track: PlaylistArchiveTrack, *, used_names: set[str]) -> str:
    title = _sanitize_filename_component(track.title, fallback=f"Track {track.source_index:02d}")
    artist = _sanitize_filename_component(track.artist_display, fallback="")
    extension = _audio_extension_from_url(track.audio_url)

    parts = [f"{track.source_index:02d}"]
    if artist:
        parts.append(artist)
    parts.append(title)

    stem = " - ".join(parts)[:180].rstrip(" ._-") or f"{track.source_index:02d}"
    candidate = f"{stem}{extension}"
    counter = 2
    while candidate.casefold() in used_names:
        candidate = f"{stem} ({counter}){extension}"
        counter += 1

    used_names.add(candidate.casefold())
    return candidate


def _estimate_zip_entry_size(*, filename: str, file_size: int) -> int:
    return file_size + len(filename.encode("utf-8")) + _ZIP_ENTRY_OVERHEAD_BYTES


async def write_playlist_archives(
    *,
    tracks: list[PlaylistArchiveTrack],
    output_dir: Path,
    max_archive_size_bytes: int,
    client: httpx.AsyncClient | None = None,
) -> PlaylistArchiveBatchSummary:
    output_dir.mkdir(parents=True, exist_ok=True)
    used_names: set[str] = set()
    skipped: list[PlaylistArchiveSkippedTrack] = []
    parts: list[PlaylistArchivePart] = []
    added_count = 0
    close_client = False
    part_index = 0
    current_archive: zipfile.ZipFile | None = None
    current_archive_path: Path | None = None
    current_added_count = 0
    current_estimated_size = 0

    if client is None:
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=60.0, write=60.0, pool=10.0),
            follow_redirects=True,
        )
        close_client = True

    def start_part() -> None:
        nonlocal part_index, current_archive, current_archive_path, current_added_count, current_estimated_size
        part_index += 1
        current_archive_path = output_dir / f"playlist_part_{part_index:02d}.zip"
        current_archive = zipfile.ZipFile(
            current_archive_path,
            mode="w",
            compression=zipfile.ZIP_STORED,
        )
        current_added_count = 0
        current_estimated_size = 0

    def finish_part() -> None:
        nonlocal current_archive, current_archive_path, current_added_count, current_estimated_size
        if current_archive is None or current_archive_path is None:
            return

        current_archive.close()
        if current_added_count > 0:
            parts.append(
                PlaylistArchivePart(
                    local_path=current_archive_path,
                    added_count=current_added_count,
                    size_bytes=current_archive_path.stat().st_size,
                )
            )
        else:
            current_archive_path.unlink(missing_ok=True)

        current_archive = None
        current_archive_path = None
        current_added_count = 0
        current_estimated_size = 0

    try:
        for track in tracks:
            filename = build_playlist_track_filename(track, used_names=used_names)
            temp_path: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="wb",
                    delete=False,
                    dir=output_dir,
                    suffix=_audio_extension_from_url(track.audio_url),
                ) as tmp_file:
                    temp_path = Path(tmp_file.name)
                    async with client.stream("GET", track.audio_url) as response:
                        response.raise_for_status()
                        async for chunk in response.aiter_bytes():
                            tmp_file.write(chunk)

                estimated_entry_size = _estimate_zip_entry_size(
                    filename=filename,
                    file_size=temp_path.stat().st_size,
                )
                if estimated_entry_size > max_archive_size_bytes:
                    skipped.append(
                        PlaylistArchiveSkippedTrack(
                            source_index=track.source_index,
                            title=track.title,
                            audio_url=track.audio_url,
                            reason="Track exceeds the Discord attachment size limit by itself.",
                        )
                    )
                    continue

                if current_archive is None:
                    start_part()

                if (
                    current_archive is not None
                    and current_added_count > 0
                    and current_estimated_size + estimated_entry_size > max_archive_size_bytes
                ):
                    finish_part()
                    start_part()

                assert current_archive is not None
                current_archive.write(temp_path, arcname=filename)
                current_added_count += 1
                current_estimated_size += estimated_entry_size
                added_count += 1
            except Exception as exc:
                skipped.append(
                    PlaylistArchiveSkippedTrack(
                        source_index=track.source_index,
                        title=track.title,
                        audio_url=track.audio_url,
                        reason=f"{type(exc).__name__}: {exc}",
                    )
                )
            finally:
                if temp_path is not None:
                    temp_path.unlink(missing_ok=True)
    finally:
        finish_part()
        if close_client:
            await client.aclose()

    return PlaylistArchiveBatchSummary(
        parts=tuple(parts),
        added_count=added_count,
        skipped=tuple(skipped),
    )


async def write_playlist_archive(
    *,
    tracks: list[PlaylistArchiveTrack],
    archive_path: Path,
    client: httpx.AsyncClient | None = None,
) -> PlaylistArchiveSummary:
    batch_summary = await write_playlist_archives(
        tracks=tracks,
        output_dir=archive_path.parent,
        max_archive_size_bytes=1_000_000_000,
        client=client,
    )
    if batch_summary.part_count == 0:
        return PlaylistArchiveSummary(
            added_count=batch_summary.added_count,
            skipped=batch_summary.skipped,
        )

    batch_summary.parts[0].local_path.replace(archive_path)
    return PlaylistArchiveSummary(
        added_count=batch_summary.added_count,
        skipped=batch_summary.skipped,
    )
