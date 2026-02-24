import asyncio
import hashlib
from pathlib import Path
import sys
import subprocess

from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT / "packages" / "core"))
sys.path.append(str(ROOT / "packages" / "infra"))

from jukebotx_infra.media.video_gif_storage import MinioVideoGifStorage, VideoGifStorageConfig


class FakeS3Client:
    def __init__(self) -> None:
        self.upload_calls: list[tuple[str, str, str, dict]] = []

    def head_object(self, *, Bucket: str, Key: str) -> None:
        raise ClientError({"Error": {"Code": "404"}}, "HeadObject")

    def upload_file(self, filename: str, bucket: str, key: str, ExtraArgs: dict) -> None:
        self.upload_calls.append((filename, bucket, key, ExtraArgs))


class FakeSession:
    def __init__(self, client: FakeS3Client) -> None:
        self._client = client

    def client(self, *_args, **_kwargs) -> FakeS3Client:
        return self._client


def test_mp4_to_gif_uploads_to_minio(monkeypatch) -> None:
    fake_client = FakeS3Client()

    import boto3

    monkeypatch.setattr(boto3.session, "Session", lambda: FakeSession(fake_client))

    def _fake_run(*_args, **_kwargs):
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)

    storage = MinioVideoGifStorage(
        VideoGifStorageConfig(
            provider="s3",
            bucket="media",
            prefix="media-gifs",
            region="",
            endpoint_url="http://localhost:9000",
            access_key_id="minio",
            secret_access_key="minio123",
            public_base_url="https://cdn.example.com",
            ffmpeg_path="ffmpeg",
        )
    )

    video_url = "https://cdn.suno.ai/video.mp4"
    result = asyncio.run(storage.mp4_to_gif(video_url=video_url))

    digest = hashlib.sha256(video_url.encode("utf-8")).hexdigest()
    expected_key = f"media-gifs/{digest}.gif"

    assert result == f"https://cdn.example.com/{expected_key}"
    assert len(fake_client.upload_calls) == 1
    _filename, bucket, key, extra_args = fake_client.upload_calls[0]
    assert bucket == "media"
    assert key == expected_key
    assert extra_args == {"ContentType": "image/gif"}
