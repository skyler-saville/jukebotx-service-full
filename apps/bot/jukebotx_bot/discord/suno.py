from __future__ import annotations

import re
from collections.abc import Iterable
from urllib.parse import urlparse

_URL_RE = re.compile(r"https?://[^\s<>]+")
_SUNO_HOSTS = {"suno.com", "www.suno.com", "app.suno.ai"}
_TRAILING_PUNCT = ".,)>]}'\""


def _normalize_url(url: str) -> str:
    return url.rstrip(_TRAILING_PUNCT)


def _is_suno_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and parsed.netloc in _SUNO_HOSTS


def extract_suno_urls(message_content: str) -> list[str]:
    if not message_content:
        return []

    matches = _URL_RE.findall(message_content)
    return _dedupe_preserve_order(
        _normalize_url(match) for match in matches if _is_suno_url(_normalize_url(match))
    )


def _dedupe_preserve_order(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered
