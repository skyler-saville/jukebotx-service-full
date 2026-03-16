from __future__ import annotations

import zipfile

import httpx
import pytest

from jukebotx_bot.discord.playlist_download import (
    build_playlist_archive_part_filename,
    PlaylistArchiveTrack,
    build_playlist_archive_name,
    build_playlist_track_filename,
    write_playlist_archives,
    write_playlist_archive,
)


def test_build_playlist_archive_name_uses_playlist_id() -> None:
    name = build_playlist_archive_name("https://suno.com/playlist/abc-123/?ref=discord")

    assert name == "suno_playlist_abc-123.zip"


def test_build_playlist_track_filename_sanitizes_and_dedupes() -> None:
    used_names: set[str] = set()
    track = PlaylistArchiveTrack(
        source_index=1,
        title="Rise / Fall?",
        artist_display="DJ: Test",
        audio_url="https://cdn.test/rise.mp3?download=1",
    )

    first = build_playlist_track_filename(track, used_names=used_names)
    second = build_playlist_track_filename(track, used_names=used_names)

    assert first == "01 - DJ Test - Rise Fall.mp3"
    assert second == "01 - DJ Test - Rise Fall (2).mp3"


def test_build_playlist_archive_part_filename_numbers_multi_part_exports() -> None:
    assert (
        build_playlist_archive_part_filename(
            "suno_playlist_abc.zip",
            part_index=2,
            part_count=12,
        )
        == "suno_playlist_abc_part02of12.zip"
    )


@pytest.mark.asyncio
async def test_write_playlist_archive_downloads_tracks_and_skips_failures(tmp_path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://cdn.test/one.mp3":
            return httpx.Response(200, content=b"one-bytes", request=request)
        if str(request.url) == "https://cdn.test/two.mp3":
            return httpx.Response(200, content=b"two-bytes", request=request)
        return httpx.Response(404, text="missing", request=request)

    archive_path = tmp_path / "playlist.zip"
    tracks = [
        PlaylistArchiveTrack(
            source_index=1,
            title="Song One",
            artist_display="Artist",
            audio_url="https://cdn.test/one.mp3",
        ),
        PlaylistArchiveTrack(
            source_index=2,
            title="Song Two",
            artist_display=None,
            audio_url="https://cdn.test/missing.mp3",
        ),
        PlaylistArchiveTrack(
            source_index=3,
            title=None,
            artist_display=None,
            audio_url="https://cdn.test/two.mp3",
        ),
    ]

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        summary = await write_playlist_archive(tracks=tracks, archive_path=archive_path, client=client)

    assert summary.added_count == 2
    assert summary.skipped_count == 1
    assert summary.skipped[0].source_index == 2

    with zipfile.ZipFile(archive_path) as archive:
        assert archive.namelist() == [
            "01 - Artist - Song One.mp3",
            "03 - Track 03.mp3",
        ]
        assert archive.read("01 - Artist - Song One.mp3") == b"one-bytes"
        assert archive.read("03 - Track 03.mp3") == b"two-bytes"


@pytest.mark.asyncio
async def test_write_playlist_archives_splits_large_exports_into_parts(tmp_path) -> None:
    payload = b"x" * 120

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload, request=request)

    tracks = [
        PlaylistArchiveTrack(
            source_index=1,
            title="Song One",
            artist_display="Artist",
            audio_url="https://cdn.test/one.mp3",
        ),
        PlaylistArchiveTrack(
            source_index=2,
            title="Song Two",
            artist_display="Artist",
            audio_url="https://cdn.test/two.mp3",
        ),
        PlaylistArchiveTrack(
            source_index=3,
            title="Song Three",
            artist_display="Artist",
            audio_url="https://cdn.test/three.mp3",
        ),
    ]

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        summary = await write_playlist_archives(
            tracks=tracks,
            output_dir=tmp_path,
            max_archive_size_bytes=850,
            client=client,
        )

    assert summary.added_count == 3
    assert summary.skipped_count == 0
    assert summary.part_count == 2
    assert [part.added_count for part in summary.parts] == [2, 1]
    assert all(part.local_path.exists() for part in summary.parts)
