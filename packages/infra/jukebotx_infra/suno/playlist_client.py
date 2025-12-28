from __future__ import annotations

import html as html_lib
import re
from dataclasses import dataclass
from typing import Final

import httpx

from jukebotx_infra.suno.client import SunoScrapeError  # reuse your existing error type


@dataclass(frozen=True)
class SunoPlaylistData:
    """
    Parsed playlist data.

    Attributes:
        playlist_url: The playlist URL that was fetched.
        mp3_urls: De-duplicated mp3 URLs found on the page, in discovery order.
    """
    playlist_url: str
    mp3_urls: list[str]


# Matches *real* og:audio meta tags in the server HTML
_OG_AUDIO_META_RE: Final[re.Pattern[str]] = re.compile(
    r"""<meta\s+property=["']og:audio["']\s+content=["'](?P<url>https?://[^"']+?\.mp3)["']\s*/?>""",
    re.IGNORECASE,
)

# Matches Next.js streaming payload fragments:
#   self.__next_f.push([1,"..."])
_NEXT_F_PUSH_STR_RE: Final[re.Pattern[str]] = re.compile(
    r"""self\.__next_f\.push\(\s*\[\s*\d+\s*,\s*"(?P<payload>(?:\\.|[^"\\])*)"\s*\]\s*\)""",
    re.DOTALL,
)

# Inside payload fragments, the meta tag markup may appear as text. Reuse a slightly looser pattern.
_OG_AUDIO_IN_TEXT_RE: Final[re.Pattern[str]] = re.compile(
    r"""<meta\s+property=["']og:audio["']\s+content=["'](?P<url>https?://[^"']+?\.mp3)["']""",
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
    # Order matters: unescape entities after backslash decoding is fine,
    # but also safe to apply before. We do both lightly.
    text = fragment.replace("\\n", "\n").replace('\\"', '"')
    text = html_lib.unescape(text)
    return text


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


class HttpxSunoPlaylistClient:
    """
    Playlist scraper that extracts MP3 URLs from a Suno playlist page using HTTP.

    Strategy:
      1) Find og:audio tags in raw HTML
      2) Find og:audio tags inside __next_f streamed payload fragments
      3) De-duplicate and return

    Why this works:
      - Even if the DOM is hydrated client-side, the server often embeds metadata
        inside streamed payload text (which your smoke test already proved for lyrics).
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
        Fetch a Suno playlist page and extract MP3 URLs.

        Args:
            playlist_url: URL like https://suno.com/playlist/<uuid>

        Returns:
            SunoPlaylistData containing mp3_urls.

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

        mp3_urls: list[str] = []

        # 1) Raw HTML meta tags
        for m in _OG_AUDIO_META_RE.finditer(page_html):
            mp3_urls.append(m.group("url").strip())

        # 2) Streamed payload fragments (Next.js)
        payloads = _extract_next_f_payloads(page_html)
        for frag in payloads:
            decoded = _decode_stream_fragment(frag)
            for m in _OG_AUDIO_IN_TEXT_RE.finditer(decoded):
                mp3_urls.append(m.group("url").strip())

        mp3_urls = _dedupe_preserve_order(mp3_urls)

        return SunoPlaylistData(playlist_url=playlist_url, mp3_urls=mp3_urls)
