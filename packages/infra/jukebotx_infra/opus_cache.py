from __future__ import annotations

import time
from pathlib import Path
from uuid import UUID


class OpusCacheService:
    def __init__(self, *, cache_dir: Path, ttl_seconds: int) -> None:
        self._cache_dir = cache_dir
        self._ttl_seconds = ttl_seconds

    @property
    def cache_dir(self) -> Path:
        return self._cache_dir

    def cache_path(self, *, track_id: UUID) -> Path:
        return self._cache_dir / f"{track_id}.opus"

    def ensure_cache_dir(self) -> None:
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    def is_fresh(self, path: Path) -> bool:
        if self._ttl_seconds <= 0:
            return True
        try:
            age = time.time() - path.stat().st_mtime
        except FileNotFoundError:
            return False
        return age < self._ttl_seconds

    def is_cached(self, *, track_id: UUID) -> bool:
        path = self.cache_path(track_id=track_id)
        return path.exists() and self.is_fresh(path)
