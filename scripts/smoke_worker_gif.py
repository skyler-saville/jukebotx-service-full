from __future__ import annotations

import argparse
import asyncio
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.request import Request, urlopen

from jukebotx_infra.storage import OpusStorageConfig, OpusStorageService
from jukebotx_infra.suno.client import HttpxSunoClient


def _download_remote_file(*, url: str, destination: Path) -> None:
    request = Request(url, headers={"User-Agent": "jukebotx-media-worker/1.0"})
    with urlopen(request, timeout=30) as response:
        with destination.open("wb") as handle:
            shutil.copyfileobj(response, handle)


def _transcode_video_to_gif(
    *,
    ffmpeg_path: str,
    input_path: Path,
    output_path: Path,
    fps: int,
    width: int,
) -> None:
    filter_graph = f"fps={fps},scale={width}:-1:flags=lanczos"
    command = [
        ffmpeg_path,
        "-y",
        "-i",
        str(input_path),
        "-vf",
        filter_graph,
        "-loop",
        "0",
        str(output_path),
    ]
    subprocess.run(command, check=True, capture_output=True, text=True)


def _build_storage() -> OpusStorageService:
    import os

    return OpusStorageService(
        OpusStorageConfig(
            provider=os.environ.get("OPUS_STORAGE_PROVIDER", "s3"),
            bucket=os.environ.get("OPUS_STORAGE_BUCKET", ""),
            prefix=os.environ.get("OPUS_STORAGE_PREFIX", "opus"),
            region=os.environ.get("OPUS_STORAGE_REGION", ""),
            endpoint_url=os.environ.get("OPUS_STORAGE_ENDPOINT_URL", ""),
            access_key_id=os.environ.get("OPUS_STORAGE_ACCESS_KEY_ID", ""),
            secret_access_key=os.environ.get("OPUS_STORAGE_SECRET_ACCESS_KEY", ""),
            public_base_url=os.environ.get("OPUS_STORAGE_PUBLIC_BASE_URL", ""),
            signed_url_ttl_seconds=int(os.environ.get("OPUS_STORAGE_SIGNED_URL_TTL_SECONDS", "900")),
            ttl_seconds=int(os.environ.get("OPUS_STORAGE_TTL_SECONDS", "604800")),
        )
    )


async def _resolve_video_url(input_url: str) -> str:
    if "suno.com/" not in input_url:
        return input_url
    client = HttpxSunoClient()
    data = await client.fetch_track(input_url)
    if not data.video_url:
        raise SystemExit("Resolved Suno track does not contain a video_url")
    return data.video_url


async def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke test GIF conversion path used by worker phase 2.")
    parser.add_argument("url", help="Suno track/share URL or direct MP4 URL")
    parser.add_argument("--ffmpeg", default="ffmpeg", help="Path to ffmpeg binary")
    parser.add_argument("--fps", type=int, default=10, help="Output GIF frames per second")
    parser.add_argument("--width", type=int, default=512, help="Output GIF width in pixels")
    parser.add_argument("--upload", action="store_true", help="Upload generated GIF to configured object storage")
    parser.add_argument(
        "--storage-prefix",
        default="media/gif-smoke",
        help="Storage prefix for uploaded smoke GIF",
    )
    args = parser.parse_args()

    video_url = await _resolve_video_url(args.url)
    print("Input URL:", args.url)
    print("Video URL:", video_url)

    with tempfile.TemporaryDirectory(prefix="jukebotx-gif-smoke-") as tmp_dir:
        tmp_path = Path(tmp_dir)
        input_path = tmp_path / "input.mp4"
        output_path = tmp_path / "output.gif"

        print("Downloading video...")
        await asyncio.to_thread(_download_remote_file, url=video_url, destination=input_path)
        print("Transcoding GIF...")
        await asyncio.to_thread(
            _transcode_video_to_gif,
            ffmpeg_path=args.ffmpeg,
            input_path=input_path,
            output_path=output_path,
            fps=args.fps,
            width=args.width,
        )
        size_bytes = output_path.stat().st_size
        print("GIF generated:", output_path)
        print("GIF size bytes:", size_bytes)

        if not args.upload:
            print("Upload skipped (pass --upload to test storage upload)")
            return

        storage = _build_storage()
        if not storage.is_enabled:
            raise SystemExit("--upload set but storage is not enabled/configured via OPUS_STORAGE_* env vars")

        object_key = f"{args.storage_prefix.strip('/')}/smoke.gif"
        storage.upload_media_file(
            local_path=output_path,
            object_key=object_key,
            content_type="image/gif",
        )
        public_url = storage.public_url(object_key=object_key) or storage.get_access_url(object_key=object_key)
        print("Uploaded object key:", object_key)
        print("Uploaded URL:", public_url)


if __name__ == "__main__":
    asyncio.run(main())
