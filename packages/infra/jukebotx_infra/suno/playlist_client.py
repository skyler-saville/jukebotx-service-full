from __future__ import annotations

import html as html_lib
import re
from dataclasses import dataclass
from typing import Final

import httpx

from jukebotx_infra.suno.client import SunoScrapeError


@dataclass(frozen=True)
class SunoPlaylistItem:
    """
    A single playlist entry discovered from a Suno playlist page.

    Note:
    - mp3_url is the only thing we can count on for playback.
    - suno_track_url is "best-effort" (used for metadata enrichment).
    - source_index preserves playlist ordering as observed on the page.
    """
    source_index: int
    mp3_url: str
    suno_track_url: str | None


@dataclass(frozen=True)
class SunoPlaylistData:
    """
    Parsed playlist page result.

    items are ordered by source_index.
    """
    playlist_url: str
    items: list[SunoPlaylistItem]

    @property
    def mp3_urls(self) -> list[str]:
        return [it.mp3_url for it in self.items]


# --- OpenGraph audio meta tag ---
_OG_AUDIO_META_RE: Final[re.Pattern[str]] = re.compile(
    r"""<meta\s+property=["']og:audio["']\s+content=["'](?P<url>https?://[^"']+?\.mp3)["']\s*/?>""",
    re.IGNORECASE,
)

# --- Next.js streaming payload fragments ---
_NEXT_F_PUSH_STR_RE: Final[re.Pattern[str]] = re.compile(
    r"""self\.__next_f\.push\(\s*\[\s*\d+\s*,\s*"(?P<payload>(?:\\.|[^"\\])*)"\s*\]\s*\)""",
    re.DOTALL,
)

_MP3_UUID_RE: Final[re.Pattern[str]] = re.compile(
    r"""https?://cdn\d+\.suno\.ai/(?P<uuid>[0-9a-fA-F-]{36})\.mp3""",
    re.IGNORECASE,
)

# og:audio markup inside streamed payload text
_OG_AUDIO_IN_TEXT_RE: Final[re.Pattern[str]] = re.compile(
    r"""<meta\s+property=["']og:audio["']\s+content=["'](?P<url>https?://[^"']+?\.mp3)["']""",
    re.IGNORECASE,
)

# Track page URLs might appear in streamed payloads as either absolute or relative links.
# This is intentionally conservative: it captures /s/<something> and normalizes it.
_TRACK_URL_RE: Final[re.Pattern[str]] = re.compile(
    r"""(?P<url>(?:https?://suno\.com)?/s/[A-Za-z0-9_-]+)""",
    re.IGNORECASE,
)


def _extract_next_f_payloads(page_html: str) -> list[str]:
    """
    Extract raw (escaped) string payload fragments from Next.js streaming pushes.
    """
    return [m.group("payload") for m in _NEXT_F_PUSH_STR_RE.finditer(page_html)]


def _decode_stream_fragment(fragment: str) -> str:
    """
    Decode a streamed fragment into normal text.

    - Unescape HTML entities.
    - Convert \\n -> newlines.
    - Convert \\" -> ".
    """
    text = fragment.replace("\\n", "\n").replace('\\"', '"')
    return html_lib.unescape(text)


def _dedupe_preserve_order(urls: list[str]) -> list[str]:
    """
    De-duplicate while preserving discovery order.
    """
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        if u not in seen:
            out.append(u)
            seen.add(u)
    return out


def _normalize_track_url(url: str) -> str:
    """
    Normalize a track URL to an absolute https://suno.com/s/<id> form.
    """
    u = url.strip()
    if u.startswith("http://"):
        u = "https://" + u[len("http://") :]
    if u.startswith("/s/"):
        return f"https://suno.com{u}"
    if u.startswith("https://suno.com/s/"):
        return u
    if u.startswith("suno.com/s/"):
        return "https://" + u
    # Fallback: return as-is (shouldn't happen if regex is correct)
    return u


def _extract_mp3_urls(page_html: str) -> list[str]:
    """
    Extract mp3 URLs from:
    - raw HTML <meta property="og:audio" ...>
    - streamed payload fragments containing the same markup
    """
    mp3_urls: list[str] = []

    # 1) raw HTML meta tags
    for m in _OG_AUDIO_META_RE.finditer(page_html):
        mp3_urls.append(m.group("url").strip())

    # 2) streamed payloads
    for frag in _extract_next_f_payloads(page_html):
        decoded = _decode_stream_fragment(frag)
        for m in _OG_AUDIO_IN_TEXT_RE.finditer(decoded):
            mp3_urls.append(m.group("url").strip())

    return _dedupe_preserve_order(mp3_urls)


def _extract_track_urls_from_stream(page_html: str) -> list[str]:
    """
    Best-effort extraction of track page URLs from streamed payload fragments.

    WARNING:
    - This may return 0 results on some playlists (Suno can change markup).
    - Even when found, mapping mp3 -> track_url may not be 1:1 without more logic.
    """
    urls: list[str] = []
    for frag in _extract_next_f_payloads(page_html):
        decoded = _decode_stream_fragment(frag)
        for m in _TRACK_URL_RE.finditer(decoded):
            urls.append(_normalize_track_url(m.group("url")))
    return _dedupe_preserve_order(urls)

def _derive_song_url_from_mp3(mp3_url: str) -> str | None:
    """
    Best-effort: derive https://suno.com/song/<uuid> from an mp3 URL.
    Returns None if it doesn't match the expected pattern.
    """
    m = _MP3_UUID_RE.match(mp3_url.strip())
    if not m:
        return None
    track_id = m.group("uuid").lower()
    return f"https://suno.com/song/{track_id}"

class HttpxSunoPlaylistClient:
    """
    Playlist scraper using HTTPX.

    Output:
      - Ordered items with mp3_url always populated.
      - Optional suno_track_url if we can discover track links in the streamed payload.

    Design choice:
      - We DO NOT try to pair mp3_url -> suno_track_url unless we can prove a stable mapping.
        Returning a list of discovered track URLs is still useful for enrichment attempts,
        but you should treat suno_track_url on items as optional until verified.
    """

    def __init__(
        self,
        *,
        timeout_seconds: float = 10.0,
        user_agent: str = "Mozilla/5.0 (compatible; JukeBotx/1.0)",
    ) -> None:
        self._timeout = httpx.Timeout(timeout_seconds)
        self._headers = {"User-Agent": user_agent}

    async def fetch_playlist(self, playlist_url: str) -> SunoPlaylistData:
        """
        Fetch a Suno playlist page and extract track entries.

        Args:
            playlist_url: URL like https://suno.com/playlist/<uuid>

        Returns:
            SunoPlaylistData with ordered items.

        Raises:
            SunoScrapeError: on network errors / non-200 responses.
        """
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                headers=self._headers,
                follow_redirects=True,
            ) as client:
                resp = await client.get(playlist_url)
                resp.raise_for_status()
                page_html = resp.text
        except Exception as exc:
            raise SunoScrapeError(
                f"Failed to fetch Suno playlist page: {playlist_url}. Error: {exc}"
            ) from exc

        mp3_urls = _extract_mp3_urls(page_html)
        track_urls = _extract_track_urls_from_stream(page_html)

        # Conservative pairing:
        # Only pair by index if counts match exactly.
        # If they don't match, we leave suno_track_url=None for all items to avoid wrong data.
        can_pair = len(track_urls) == len(mp3_urls) and len(mp3_urls) > 0

        items: list[SunoPlaylistItem] = []
        for idx, mp3 in enumerate(mp3_urls, start=1):
            items.append(
                SunoPlaylistItem(
                    source_index=idx,
                    mp3_url=mp3,
                    suno_track_url=_derive_song_url_from_mp3(mp3),
                )
            )


        return SunoPlaylistData(playlist_url=playlist_url, items=items)
