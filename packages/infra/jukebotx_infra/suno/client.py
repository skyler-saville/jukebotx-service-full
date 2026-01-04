from __future__ import annotations

import asyncio
import html as html_lib
import logging
import random
import re
from dataclasses import dataclass
from typing import Final

import httpx


_LOG = logging.getLogger(__name__)

@dataclass(frozen=True)
class SunoTrackData:
    """
    Normalized metadata scraped from a Suno track page (or derived from an MP3 URL).
    """
    suno_url: str
    title: str | None
    artist_display: str | None
    artist_username: str | None
    lyrics: str | None
    image_url: str | None
    video_url: str | None
    mp3_url: str | None

    @property
    def media_url(self) -> str | None:
        return self.image_url or self.video_url


class SunoScrapeError(RuntimeError):
    """Raised when Suno metadata cannot be fetched or parsed."""


# --- Meta tag extraction (lightweight, fast) ---
_META_TAG_RE: Final[re.Pattern[str]] = re.compile(
    r"""<meta\s+(?:property|name)\s*=\s*["'](?P<key>[^"']+)["']\s+content\s*=\s*["'](?P<val>[^"']*)["']\s*/?>""",
    re.IGNORECASE,
)

_TITLE_RE: Final[re.Pattern[str]] = re.compile(
    r"<title>(?P<title>.*?)</title>",
    re.IGNORECASE | re.DOTALL,
)

# --- Next.js streaming payload ---
_NEXT_F_PUSH_STR_RE: Final[re.Pattern[str]] = re.compile(
    r"""self\.__next_f\.push\(\s*\[\s*\d+\s*,\s*"(?P<payload>(?:\\.|[^"\\])*)"\s*\]\s*\)""",
    re.DOTALL,
)

# --- URL normalization helpers ---
_SUNO_S_URL_RE: Final[re.Pattern[str]] = re.compile(r"^https?://suno\.com/s/[A-Za-z0-9_-]+/?$", re.IGNORECASE)
_SUNO_SONG_URL_RE: Final[re.Pattern[str]] = re.compile(
    r"^https?://suno\.com/song/(?P<uuid>[0-9a-fA-F-]{36})/?$", re.IGNORECASE
)
_MP3_URL_RE: Final[re.Pattern[str]] = re.compile(
    r"^https?://cdn\d+\.suno\.ai/(?P<uuid>[0-9a-fA-F-]{36})\.mp3$", re.IGNORECASE
)


def _strip_html_whitespace(text: str) -> str:
    return " ".join(text.split()).strip()


def _parse_meta_tags(page_html: str) -> dict[str, str]:
    tags: dict[str, str] = {}
    for m in _META_TAG_RE.finditer(page_html):
        key = m.group("key").strip()
        val = html_lib.unescape(m.group("val").strip())
        if key and val:
            tags[key] = val
    return tags


def _normalize_track_url(url: str) -> str:
    """
    Normalize Suno URLs into a canonical form. Accepts:
      - https://suno.com/s/<id>
      - https://suno.com/song/<uuid>
      - https://cdn1.suno.ai/<uuid>.mp3  (converted to https://suno.com/song/<uuid>)
    """
    u = url.strip()

    # Convert MP3 URL -> song URL (best effort)
    m_mp3 = _MP3_URL_RE.match(u)
    if m_mp3:
        track_id = m_mp3.group("uuid").lower()
        return f"https://suno.com/song/{track_id}"

    # Normalize scheme + trailing slash
    if u.startswith("http://"):
        u = "https://" + u[len("http://") :]
    u = u.rstrip("/")

    # Keep /s/ or /song/ as-is
    if _SUNO_S_URL_RE.match(u) or _SUNO_SONG_URL_RE.match(u):
        return u

    # If someone passed "suno.com/..." without scheme
    if u.startswith("suno.com/"):
        return "https://" + u

    return u


def _decode_stream_fragment(fragment: str) -> str:
    """
    Decode a Next.js streamed fragment into normal text.
    """
    # Most useful decode steps for your observed payload:
    # - \\n -> newline
    # - \\" -> "
    # - HTML entities
    text = fragment.replace("\\n", "\n").replace('\\"', '"')
    return html_lib.unescape(text)


def _extract_next_f_payloads(page_html: str) -> list[str]:
    return [m.group("payload") for m in _NEXT_F_PUSH_STR_RE.finditer(page_html)]


def _looks_like_ui_or_boilerplate(text: str) -> bool:
    lowered = text.lower()
    bad_markers = (
        "cookie",
        "privacy",
        "terms",
        "sign up",
        "log in",
        "pricing",
        "subscribe",
        "download",
        "app store",
        "google play",
        "enable cookies",
    )
    return any(m in lowered for m in bad_markers)


def _score_lyrics_candidate(text: str) -> int:
    """
    Score a candidate block as lyrics WITHOUT requiring [Verse]/[Chorus] markers.
    """
    t = text.strip()
    if len(t) < 200:
        return 0
    if _looks_like_ui_or_boilerplate(t):
        return 0

    lines = [ln for ln in t.splitlines() if ln.strip()]
    if len(lines) < 6:
        return 0

    score = 0

    # multi-line matters
    score += min(250, len(lines) * 10)

    # prefer moderate line lengths; penalize giant single-line dumps
    moderate = sum(1 for ln in lines if 10 <= len(ln) <= 120)
    very_long = sum(1 for ln in lines if len(ln) > 200)
    score += min(250, moderate * 12)
    score -= min(250, very_long * 35)

    # mild boost for structural markers if present, but not required
    lowered = t.lower()
    if any(tag in lowered for tag in ("[verse", "[chorus", "[bridge", "[outro", "[intro")):
        score += 120

    # size helps but capped
    score += min(200, len(t) // 35)

    return score


def _normalize_text_block(text: str) -> str:
    """
    Normalize escaped newlines + whitespace for scoring/returning.
    """
    t = _decode_stream_fragment(text)
    # Clean trailing whitespace per line, keep line breaks
    lines = [ln.rstrip() for ln in t.splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines).strip()


def _extract_lyrics_from_next_stream(page_html: str) -> str | None:
    """
    Extract lyrics by scoring each Next.js streamed payload fragment individually.

    We do not concatenate fragments because most are framework plumbing.
    """
    payloads = _extract_next_f_payloads(page_html)
    if not payloads:
        return None

    best: str | None = None
    best_score = 0

    for raw in payloads:
        normalized = _normalize_text_block(raw)
        sc = _score_lyrics_candidate(normalized)
        if sc > best_score:
            best_score = sc
            best = normalized

    # Threshold tuned to avoid returning random app text.
    # If you find this too strict on some pages, drop to ~340.
    if best and best_score >= 420:
        return best

    return None


def _parse_title_artist_from_description(description: str | None) -> tuple[str | None, str | None, str | None]:
    """
    Best-effort parsing:
      "<title> by <artist display>. ... (@username)"

    Suno sometimes mutates this. We keep it conservative.
    """
    if not description:
        return None, None, None

    desc = description.strip()
    if " by " not in desc:
        return None, None, None

    left, right = desc.split(" by ", 1)
    song_title = left.strip() or None

    # Extract username (optional)
    artist_username = None
    at_match = re.search(r"\(@(?P<handle>[^)]+)\)", right)
    if at_match:
        artist_username = at_match.group("handle").strip() or None
        right = re.sub(r"\s*\(@[^)]+\)\s*", "", right).strip()

    # Trim promo sentence if present
    promo = "listen and make your own on suno"
    lowered_right = right.lower()
    promo_index = lowered_right.find(promo)
    if promo_index != -1:
        right = right[:promo_index].rstrip(" .").strip()

    artist_display = right.strip() or None
    return song_title, artist_display, artist_username


class HttpxSunoClient:
    """
    Suno metadata client that supports:
      - /s/<id> pages
      - /song/<uuid> pages
      - direct mp3 URLs (converted to /song/<uuid>)

    Lyrics extraction relies on Next.js streaming payloads (self.__next_f.push).
    """

    def __init__(
        self,
        *,
        timeout: httpx.Timeout | None = None,
        user_agent: str = "Mozilla/5.0 (compatible; JukeBotx/1.0)",
        max_attempts: int = 3,
    ) -> None:
        self._timeout = timeout or httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)
        self._headers = {
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Referer": "https://suno.com/",
        }
        self._max_attempts = max(1, max_attempts)

    @staticmethod
    def _format_http_error(exc: httpx.HTTPStatusError, normalized_url: str) -> str:
        response = exc.response
        snippet = response.text[:500].replace("\n", " ").strip()
        return (
            "Failed to fetch Suno page: "
            f"{normalized_url}. Status: {response.status_code}. "
            f"Final URL: {response.url}. "
            f"Body snippet: {snippet!r}"
        )

    async def fetch_track(self, url: str) -> SunoTrackData:
        """
        Fetch and parse metadata from a Suno track URL.

        Args:
            url: Suno track URL or mp3 URL.

        Returns:
            SunoTrackData

        Raises:
            SunoScrapeError: on network failures or invalid responses.
        """
        normalized_url = _normalize_track_url(url)

        retryable_statuses = {408, 425, 429, 500, 502, 503, 504}
        page_html: str | None = None
        final_url = normalized_url

        async with httpx.AsyncClient(
            timeout=self._timeout,
            headers=self._headers,
            follow_redirects=True,
            http2=True,
        ) as client:
            last_exc: BaseException | None = None
            for attempt in range(1, self._max_attempts + 1):
                try:
                    resp = await client.get(normalized_url)
                    if resp.status_code in retryable_statuses:
                        snippet = resp.text[:300].replace("\n", " ").strip()
                        raise SunoScrapeError(
                            "Retryable Suno response. "
                            f"Status: {resp.status_code}. URL: {resp.url}. "
                            f"Body snippet: {snippet!r}"
                        )

                    try:
                        resp.raise_for_status()
                    except httpx.HTTPStatusError as exc:
                        raise SunoScrapeError(self._format_http_error(exc, normalized_url)) from exc

                    page_html = resp.text
                    final_url = str(resp.url)
                    last_exc = None
                    break
                except (httpx.ReadTimeout, httpx.ConnectTimeout) as exc:
                    last_exc = exc
                    _LOG.warning(
                        "Timeout fetching Suno page (attempt %d/%d): %s exc_repr=%r",
                        attempt,
                        self._max_attempts,
                        normalized_url,
                        exc,
                    )
                except httpx.RequestError as exc:
                    last_exc = exc
                    _LOG.warning(
                        "Request error fetching Suno page (attempt %d/%d): %s exc_type=%s exc_repr=%r",
                        attempt,
                        self._max_attempts,
                        normalized_url,
                        type(exc).__name__,
                        exc,
                    )
                except SunoScrapeError as exc:
                    last_exc = exc
                    if attempt == self._max_attempts:
                        break
                    _LOG.warning(
                        "Retrying Suno fetch (attempt %d/%d): %s",
                        attempt,
                        self._max_attempts,
                        exc,
                    )

                if attempt < self._max_attempts:
                    await asyncio.sleep((0.6 * (2 ** (attempt - 1))) + random.random() * 0.3)

            else:
                last_exc = None

        if page_html is None and last_exc is not None:
            raise SunoScrapeError(
                f"Failed to fetch Suno page after {self._max_attempts} attempts: {normalized_url}. "
                f"exc_type={type(last_exc).__name__} exc_repr={last_exc!r}"
            ) from last_exc

        if len(page_html) < 2000:
            snippet = page_html[:500].replace("\n", " ").strip()
            raise SunoScrapeError(
                "Fetched Suno page but HTML was too small to be useful. "
                f"URL: {final_url}. Length: {len(page_html)}. "
                f"Body snippet: {snippet!r}"
            )

        meta = _parse_meta_tags(page_html)

        description = meta.get("description") or meta.get("og:description")
        og_video = meta.get("og:video")
        og_image = meta.get("og:image")
        og_audio = meta.get("og:audio")

        # Parse title/artist from description first (fast), fallback to <title>
        title, artist_display, artist_username = _parse_title_artist_from_description(description)

        if not title:
            m_title = _TITLE_RE.search(page_html)
            if m_title:
                title = _strip_html_whitespace(m_title.group("title"))

        lyrics = _extract_lyrics_from_next_stream(page_html)

        return SunoTrackData(
            suno_url=final_url,  # final URL after redirects (could normalize /s -> /song or vice versa)
            title=title,
            artist_display=artist_display,
            artist_username=artist_username,
            lyrics=lyrics,
            image_url=og_image,
            video_url=og_video,
            mp3_url=og_audio,
        )
