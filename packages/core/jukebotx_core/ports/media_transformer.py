from __future__ import annotations


class MediaTransformer:
    async def mp4_to_gif(self, *, video_url: str) -> str | None:
        """Convert an MP4 URL into a GIF URL.

        Implementations should return a publicly-reachable URL for the generated GIF
        (for example an object stored in Minio/S3), or ``None`` when conversion fails.
        """
        raise NotImplementedError
