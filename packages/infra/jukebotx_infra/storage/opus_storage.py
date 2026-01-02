from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import boto3
from botocore.exceptions import ClientError


@dataclass(frozen=True)
class OpusStorageConfig:
    provider: str
    bucket: str
    prefix: str
    region: str
    endpoint_url: str
    access_key_id: str
    secret_access_key: str
    public_base_url: str
    signed_url_ttl_seconds: int
    ttl_seconds: int


class OpusStorageService:
    def __init__(self, config: OpusStorageConfig) -> None:
        self._config = config
        self._client = None
        if self.is_enabled:
            session = boto3.session.Session()
            self._client = session.client(
                "s3",
                region_name=config.region or None,
                endpoint_url=config.endpoint_url or None,
                aws_access_key_id=config.access_key_id or None,
                aws_secret_access_key=config.secret_access_key or None,
            )

    @property
    def is_enabled(self) -> bool:
        return self._config.provider == "s3" and bool(self._config.bucket)

    def object_key(self, *, track_id: UUID) -> str:
        prefix = self._config.prefix.strip("/")
        filename = f"{track_id}.opus"
        if prefix:
            return f"{prefix}/{filename}"
        return filename

    def public_url(self, *, object_key: str) -> str | None:
        if not self._config.public_base_url:
            return None
        return f"{self._config.public_base_url.rstrip('/')}/{object_key}"

    def get_access_url(self, *, object_key: str) -> str:
        public_url = self.public_url(object_key=object_key)
        if public_url:
            return public_url
        if self._client is None:
            raise RuntimeError("Storage client not configured")
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._config.bucket, "Key": object_key},
            ExpiresIn=max(self._config.signed_url_ttl_seconds, 1),
        )

    def upload_file(self, *, local_path: Path, object_key: str) -> None:
        if self._client is None:
            raise RuntimeError("Storage client not configured")
        extra_args: dict[str, str] = {"ContentType": "audio/opus"}
        if self._config.ttl_seconds > 0:
            extra_args["Expires"] = (
                datetime.now(timezone.utc) + timedelta(seconds=self._config.ttl_seconds)
            ).strftime("%a, %d %b %Y %H:%M:%S GMT")
            extra_args["Tagging"] = f"jukebotx-ttl-seconds={self._config.ttl_seconds}"
        self._client.upload_file(
            str(local_path),
            self._config.bucket,
            object_key,
            ExtraArgs=extra_args,
        )

    def delete_object(self, *, object_key: str) -> None:
        if self._client is None:
            return
        try:
            self._client.delete_object(Bucket=self._config.bucket, Key=object_key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"NoSuchKey", "404"}:
                return
            raise

    def is_fresh(self, *, object_key: str) -> bool:
        if self._client is None:
            return False
        try:
            head = self._client.head_object(Bucket=self._config.bucket, Key=object_key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"NoSuchKey", "404"}:
                return False
            raise
        if self._config.ttl_seconds <= 0:
            return True
        last_modified: datetime = head["LastModified"]
        age = datetime.now(timezone.utc) - last_modified
        if age.total_seconds() >= self._config.ttl_seconds:
            self.delete_object(object_key=object_key)
            return False
        return True
