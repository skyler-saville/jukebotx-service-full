# packages/infra/jukebotx_infra/suno/client.py
from __future__ import annotations

import html as html_lib
import json
import re
from dataclasses import dataclass
from typing import Any, Final

import httpx


@dataclass(frozen=True)
class SunoTrackData:
    """
    Normalized metadata scraped from a Suno track page.

    Reality check:
    - The HTML returned by plain HTTP may NOT contain the hydrated DOM you see in a browser.
    - Suno can embed lyrics inside Next.js streaming payload fragments:
        <script>self.__next_f.push([1,"...escaped text..."])</script>
      These contain \\n escapes (not real newlines), so DOM-based scraping fails.
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
        return self.video_url or self.image_url


class SunoScrapeError(RuntimeError):
    """Raised when Suno metadata cannot be fetched or parsed."""


# -----------------------------
# Regex / parsing primitives
# -----------------------------

_META_TAG_RE: Final[re.Pattern[str]] = re.compile(
    r"""<meta\s+(?:property|name)\s*=\s*["'](?P<key>[^"']+)["']\s+content\s*=\s*["'](?P<val>[^"']*)["']\s*/?>""",
    re.IGNORECASE,
)

_TITLE_RE: Final[re.Pattern[str]] = re.compile(
    r"<title>(?P<title>.*?)</title>",
    re.IGNORECASE | re.DOTALL,
)

_NEXT_DATA_RE: Final[re.Pattern[str]] = re.compile(
    r"""<script[^>]+id=["']__NEXT_DATA__["'][^>]*>(?P<json>.*?)</script>""",
    re.IGNORECASE | re.DOTALL,
)

_SCRIPT_JSON_RE: Final[re.Pattern[str]] = re.compile(
    r"""<script[^>]*type=["']application/json["'][^>]*>(?P<json>.*?)</script>""",
    re.IGNORECASE | re.DOTALL,
)

# Next.js streaming pushes:
#   self.__next_f.push([1,"..."])
_NEXT_F_PUSH_STR_RE: Final[re.Pattern[str]] = re.compile(
    r"""self\.__next_f\.push\(\s*\[\s*\d+\s*,\s*"(?P<payload>(?:\\.|[^"\\])*)"\s*\]\s*\)""",
    re.DOTALL,
)

# Rare fallback: lyrics in server HTML paragraph (typically not present in raw HTML).
_LYRICS_P_RE: Final[re.Pattern[str]] = re.compile(
    r"""<p[^>]*class=["'][^"']*\bwhitespace-pre-wrap\b[^"']*\bpr-6\b[^"']*["'][^>]*>(?P<body>.*?)</p>""",
    re.IGNORECASE | re.DOTALL,
)
_LYRICS_P_FALLBACK_RE: Final[re.Pattern[str]] = re.compile(
    r"""<p[^>]*class=["'][^"']*\bwhitespace-pre-wrap\b[^"']*["'][^>]*>(?P<body>.*?)</p>""",
    re.IGNORECASE | re.DOTALL,
)
_TAG_STRIP_RE: Final[re.Pattern[str]] = re.compile(r"<[^>]+>", re.DOTALL)


# -----------------------------
# Small utilities
# -----------------------------

def _strip_html_whitespace(text: str) -> str:
    """Collapse whitespace into single spaces."""
    return " ".join(text.split()).strip()


def _parse_meta_tags(page_html: str) -> dict[str, str]:
    """Extract meta tag key/value pairs from raw HTML."""
    tags: dict[str, str] = {}
    for m in _META_TAG_RE.finditer(page_html):
        key = m.group("key").strip()
        val = m.group("val").strip()
        if key and val:
            tags[key] = val
    return tags


def _parse_title_from_description(description: str | None) -> tuple[str | None, str | None, str | None]:
    """
    Suno description often looks like:
      "<song title> by <artist display>. Listen and make your own on Suno. (@username)"
    """
    if not description:
        return None, None, None

    desc = description.strip()
    if " by " not in desc:
        return None, None, None

    left, right = desc.split(" by ", 1)
    song_title = left.strip() or None

    artist_username = None
    at_match = re.search(r"\(@(?P<handle>[^)]+)\)", right)
    if at_match:
        artist_username = at_match.group("handle").strip() or None
        right = re.sub(r"\s*\(@[^)]+\)\s*", "", right).strip()

    promo = "listen and make your own on suno"
    if promo in right.lower():
        right = right.split(".", 1)[0].strip()

    artist_display = right.strip() or None
    return song_title, artist_display, artist_username


def _extract_next_data(page_html: str) -> Any | None:
    """Extract and parse Next.js __NEXT_DATA__ JSON (if present)."""
    m = _NEXT_DATA_RE.search(page_html)
    if not m:
        return None

    raw = m.group("json").strip()
    if not raw:
        return None

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _extract_next_f_payloads(page_html: str) -> list[str]:
    """
    Return string payload fragments from:
      self.__next_f.push([<n>,"<payload>"])
    """
    return [m.group("payload") for m in _NEXT_F_PUSH_STR_RE.finditer(page_html)]


def _extract_application_json_scripts(page_html: str) -> list[Any]:
    """Extract any <script type="application/json">...</script> blocks."""
    blobs: list[Any] = []
    for m in _SCRIPT_JSON_RE.finditer(page_html):
        raw = m.group("json").strip()
        if not raw:
            continue
        try:
            blobs.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return blobs


def _normalize_text_block(text: str) -> str:
    """
    Normalize candidate blocks:
    - unescape HTML entities
    - convert \\n -> newline
    - trim trailing whitespace per line
    """
    t = html_lib.unescape(text)
    t = t.replace("\\n", "\n").replace("\\t", "\t")

    lines = [ln.rstrip() for ln in t.splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines).strip()


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
        "javascript",
    )
    return any(m in lowered for m in bad_markers)


def _looks_like_next_f_plumbing(text: str) -> bool:
    """
    Hard reject obvious RSC/Next.js plumbing fragments that are not lyrics.

    This is necessary because those fragments can contain lots of \\n and moderate
    line lengths, which can fool scoring heuristics.
    """
    lowered = text.lower()
    bad = (
        "$sreact.fragment",
        "metadataboundary",
        "viewportboundary",
        "outletboundary",
        "asyncmetadataoutlet",
        "static/chunks",
        "webpack",
        "__next",
        "react-dom",
        "chunk",
        "manifest",
    )
    return any(b in lowered for b in bad)


def _score_lyrics_candidate(text: str) -> int:
    """
    Score a candidate as lyrics without requiring [Verse]/[Chorus] tags.
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

    score += min(240, len(lines) * 10)

    moderate = sum(1 for ln in lines if 10 <= len(ln) <= 120)
    very_long = sum(1 for ln in lines if len(ln) > 220)

    score += min(260, moderate * 10)
    score -= min(260, very_long * 40)

    no_period_end = sum(1 for ln in lines if not ln.strip().endswith("."))
    score += min(140, no_period_end * 6)

    score += min(200, len(t) // 45)

    return score


def _collect_text_candidates(obj: Any, *, max_depth: int = 35) -> list[str]:
    """Collect multi-line text candidates from JSON-ish structures."""
    if max_depth <= 0:
        return []

    out: list[str] = []

    if isinstance(obj, dict):
        for v in obj.values():
            out.extend(_collect_text_candidates(v, max_depth=max_depth - 1))
    elif isinstance(obj, list):
        if obj and all(isinstance(x, str) for x in obj):
            joined = "\n".join(x for x in obj if x.strip())
            if joined.strip():
                out.append(joined.strip())
        else:
            for item in obj:
                out.extend(_collect_text_candidates(item, max_depth=max_depth - 1))
    elif isinstance(obj, str):
        if "\n" in obj or "\\n" in obj:
            out.append(obj)

    return out


# -----------------------------
# Lyrics extraction
# -----------------------------

def _best_scored_text(candidates: list[str], *, threshold: int) -> str | None:
    """
    Score candidates and return the best text above threshold.
    """
    best_text: str | None = None
    best_score = 0

    for raw in candidates:
        normalized = _normalize_text_block(raw)

        # Hard reject obvious Next.js plumbing early.
        if _looks_like_next_f_plumbing(normalized):
            continue

        sc = _score_lyrics_candidate(normalized)
        if sc > best_score:
            best_score = sc
            best_text = normalized

    if best_text and best_score >= threshold:
        return best_text
    return None


def _extract_lyrics_from_next_f(page_html: str) -> str | None:
    """
    Primary: extract lyrics from Next.js streaming payload fragments.

    Strategy:
    - Extract payload fragments.
    - Score each fragment.
    - Also score adjacent pairs (lyrics can be split across two pushes).
    - Return best above threshold.
    """
    payloads = _extract_next_f_payloads(page_html)
    if not payloads:
        return None

    # 1) Single fragment candidates
    best = _best_scored_text(payloads, threshold=420)
    if best:
        return best

    # 2) Adjacent pair candidates (helps when lyrics are split)
    paired: list[str] = []
    for i in range(len(payloads) - 1):
        paired.append(payloads[i] + payloads[i + 1])

    return _best_scored_text(paired, threshold=420)


def _extract_lyrics_from_embedded_json(page_html: str) -> str | None:
    """
    Fallback: scan embedded JSON state and pick the best multi-line text block.
    """
    blobs: list[Any] = []
    next_data = _extract_next_data(page_html)
    if next_data is not None:
        blobs.append(next_data)
    blobs.extend(_extract_application_json_scripts(page_html))

    if not blobs:
        return None

    candidates: list[str] = []
    for blob in blobs:
        candidates.extend(_collect_text_candidates(blob))

    # JSON candidates are usually noisier; demand higher confidence.
    return _best_scored_text(candidates, threshold=520)


def _extract_lyrics_from_html_paragraph(page_html: str) -> str | None:
    """
    Rare fallback: lyrics in server HTML in a pre-wrap paragraph.
    """

    def clean(body: str) -> str:
        text = _TAG_STRIP_RE.sub("", body)
        return _normalize_text_block(text)

    m = _LYRICS_P_RE.search(page_html)
    if m:
        lyrics = clean(m.group("body"))
        return lyrics or None

    candidates: list[str] = []
    for pm in _LYRICS_P_FALLBACK_RE.finditer(page_html):
        candidates.append(clean(pm.group("body")))

    return _best_scored_text(candidates, threshold=520)


def _extract_lyrics(page_html: str) -> str | None:
    """
    Single entry point for lyric extraction, in priority order.
    """
    lyrics = _extract_lyrics_from_next_f(page_html)
    if lyrics:
        return lyrics

    lyrics = _extract_lyrics_from_embedded_json(page_html)
    if lyrics:
        return lyrics

    return _extract_lyrics_from_html_paragraph(page_html)


# -----------------------------
# Main client
# -----------------------------

class HttpxSunoClient:
    """
    Lightweight Suno metadata fetcher using plain HTTP.

    Extracts:
    - og:image, og:audio, og:video
    - meta description / title
    - lyrics via Next.js streaming payloads (primary)
    """

    def __init__(
        self,
        *,
        timeout_seconds: float = 10.0,
        user_agent: str = "Mozilla/5.0 (compatible; JukeBotx/1.0)",
    ) -> None:
        self._timeout = httpx.Timeout(timeout_seconds)
        self._headers = {"User-Agent": user_agent}

    async def fetch_track(self, suno_url: str) -> SunoTrackData:
        """
        Fetch and parse metadata from a Suno URL.

        Raises:
            SunoScrapeError: hard network failures / non-200 responses.
        """
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                headers=self._headers,
                follow_redirects=True,
            ) as client:
                resp = await client.get(suno_url)
                resp.raise_for_status()
                page_html = resp.text
        except Exception as exc:
            raise SunoScrapeError(
                f"Failed to fetch Suno page: {suno_url}. Error: {exc}"
            ) from exc

        meta = _parse_meta_tags(page_html)

        description = meta.get("description") or meta.get("og:description")
        og_video = meta.get("og:video")
        og_image = meta.get("og:image")
        og_audio = meta.get("og:audio")

        song_title, artist_display, artist_username = _parse_title_from_description(description)

        if not song_title:
            t = _TITLE_RE.search(page_html)
            if t:
                song_title = _strip_html_whitespace(t.group("title"))

        lyrics = _extract_lyrics(page_html)

        return SunoTrackData(
            suno_url=suno_url,
            title=song_title,
            artist_display=artist_display,
            artist_username=artist_username,
            lyrics=lyrics,
            image_url=og_image,
            video_url=og_video,
            mp3_url=og_audio,
        )
