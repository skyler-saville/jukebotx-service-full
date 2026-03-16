from __future__ import annotations

import asyncio
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from jukebotx_api.main import download_playlist_archive
from jukebotx_core.shared import (
    build_playlist_archive_download_token,
    parse_playlist_archive_download_token,
)
from jukebotx_infra.storage import StorageObjectStream


def test_playlist_archive_download_token_round_trips() -> None:
    token = build_playlist_archive_download_token(
        object_key="downloads/playlists/archive.zip",
        filename="my_playlist.zip",
        secret="secret",
        ttl_seconds=3600,
    )

    claims = parse_playlist_archive_download_token(token, "secret")

    assert claims is not None
    assert claims.object_key == "downloads/playlists/archive.zip"
    assert claims.filename == "my_playlist.zip"


@pytest.mark.asyncio
async def test_download_playlist_archive_streams_zip_bytes() -> None:
    token = build_playlist_archive_download_token(
        object_key="downloads/playlists/archive.zip",
        filename="my_playlist.zip",
        secret="secret",
        ttl_seconds=3600,
    )
    seen_object_keys: list[str] = []

    class _Storage:
        is_enabled = True

        def is_fresh(self, *, object_key: str) -> bool:
            return object_key == "downloads/playlists/archive.zip"

        def get_object_stream(self, *, object_key: str) -> StorageObjectStream:
            seen_object_keys.append(object_key)
            assert object_key == "downloads/playlists/archive.zip"
            return StorageObjectStream(
                body=BytesIO(b"zip-bytes"),
                content_type="application/zip",
                content_length=9,
            )

    response = await asyncio.wait_for(
        download_playlist_archive(
            token=token,
            settings=SimpleNamespace(session_secret="secret"),
            opus_storage=_Storage(),
        ),
        timeout=1,
    )

    assert response.status_code == 200
    assert response.media_type == "application/zip"
    assert response.headers["content-disposition"] == 'attachment; filename="my_playlist.zip"'
    assert response.headers["content-length"] == "9"
    assert seen_object_keys == ["downloads/playlists/archive.zip"]


@pytest.mark.asyncio
async def test_download_playlist_archive_rejects_invalid_token() -> None:
    class _Storage:
        is_enabled = True

        def is_fresh(self, *, object_key: str) -> bool:
            return True

        def get_object_stream(self, *, object_key: str) -> StorageObjectStream:
            raise AssertionError("should not be called")

    with pytest.raises(HTTPException) as exc_info:
        await download_playlist_archive(
            token="not-a-real-token",
            settings=SimpleNamespace(session_secret="secret"),
            opus_storage=_Storage(),
        )

    assert exc_info.value.status_code == 404
