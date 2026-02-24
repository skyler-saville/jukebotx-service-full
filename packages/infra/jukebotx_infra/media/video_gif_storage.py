from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import logging
from pathlib import Path
import subprocess
import tempfile

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from jukebotx_core.ports.media_transformer import MediaTransformer


@dataclass(frozen=True)
class VideoGifStorageConfig:
    provider: str
    bucket: str
    prefix: str
    region: str
    endpoint_url: str
    access_key_id: str
    secret_access_key: str
    public_base_url: str
    ffmpeg_path: str


class MinioVideoGifStorage(MediaTransformer):
    def __init__(self, config: VideoGifStorageConfig) -> None:
        self._config = config
        self._logger = logging.getLogger(__name__)
        self._client = None
        if self.is_enabled:
            session = boto3.session.Session()
            client_config = None
            if config.endpoint_url:
                client_config = Config(s3={"addressing_style": "path"})
            self._client = session.client(
                "s3",
                region_name=config.region or None,
                endpoint_url=config.endpoint_url or None,
                aws_access_key_id=config.access_key_id or None,
                aws_secret_access_key=config.secret_access_key or None,
                config=client_config,
            )

    @property
    def is_enabled(self) -> bool:
        return self._config.provider == "s3" and bool(self._config.bucket) and bool(self._config.public_base_url)

    async def mp4_to_gif(self, *, video_url: str) -> str | None:
        if not self.is_enabled or self._client is None:
            return None
        return await asyncio.to_thread(self._convert_and_upload, video_url)

    def _object_key(self, video_url: str) -> str:
        digest = hashlib.sha256(video_url.encode("utf-8")).hexdigest()
        prefix = self._config.prefix.strip("/")
        filename = f"{digest}.gif"
        if prefix:
            return f"{prefix}/{filename}"
        return filename

    def _convert_and_upload(self, video_url: str) -> str | None:
        if self._client is None:
            return None
        object_key = self._object_key(video_url)
        if self._exists(object_key):
            return self._public_url(object_key)

        with tempfile.TemporaryDirectory(prefix="jukebotx-gif-") as tmp_dir:
            output_path = Path(tmp_dir) / "preview.gif"
            command = [
                self._config.ffmpeg_path,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-t",
                "8",
                "-i",
                video_url,
                "-vf",
                "fps=10,scale=320:-1:flags=lanczos",
                "-loop",
                "0",
                str(output_path),
            ]
            try:
                subprocess.run(command, check=True, capture_output=True, text=True, timeout=90)
            except subprocess.CalledProcessError as exc:
                self._logger.warning("Failed to convert MP4 to GIF for %s: %s", video_url, exc.stderr)
                return None
            except subprocess.TimeoutExpired:
                self._logger.warning("Timed out converting MP4 to GIF for %s", video_url)
                return None

            try:
                self._client.upload_file(
                    str(output_path),
                    self._config.bucket,
                    object_key,
                    ExtraArgs={"ContentType": "image/gif"},
                )
            except Exception as exc:  # noqa: BLE001
                self._logger.warning("Failed to upload GIF to storage for %s: %s", video_url, exc)
                return None

        return self._public_url(object_key)

    def _exists(self, object_key: str) -> bool:
        if self._client is None:
            return False
        try:
            self._client.head_object(Bucket=self._config.bucket, Key=object_key)
            return True
        except ClientError:
            return False

    def _public_url(self, object_key: str) -> str:
        return f"{self._config.public_base_url.rstrip('/')}/{object_key}"
