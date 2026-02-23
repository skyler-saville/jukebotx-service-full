from __future__ import annotations

import logging
from typing import Iterable

from jukebotx_core.ports.suno_client import SunoClient, SunoTrackData

from .client import HttpxSunoClient, SunoScrapeError

_LOG = logging.getLogger(__name__)

_REQUIRED_FIELDS: tuple[str, ...] = ("mp3_url",)
_QUALITY_FIELDS: tuple[str, ...] = ("image_url", "title", "artist_display", "lyrics")


class PyppeteerSunoClient(SunoClient):
    """Fallback scraper using a browser runtime for pages that fail static scraping."""

    async def fetch_track(self, suno_url: str) -> SunoTrackData:
        try:
            from pyppeteer import launch
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise SunoScrapeError(
                "pyppeteer is not installed for fallback scraping"
            ) from exc

        browser = await launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        try:
            page = await browser.newPage()
            await page.setUserAgent("Mozilla/5.0 (compatible; JukeBotx/1.0)")
            response = await page.goto(
                suno_url, waitUntil="networkidle2", timeout=45_000
            )
            if response is None:
                raise SunoScrapeError(
                    f"Fallback browser returned no response for URL: {suno_url}"
                )
            if response.status >= 400:
                raise SunoScrapeError(
                    f"Fallback browser fetch failed. URL: {suno_url}. Status: {response.status}"
                )

            payload = await page.evaluate(
                """() => {
                    const metaByProp = (prop) => {
                        const el = document.querySelector(`meta[property="${prop}"]`);
                        return el?.content || null;
                    };
                    const metaByName = (name) => {
                        const el = document.querySelector(`meta[name="${name}"]`);
                        return el?.content || null;
                    };

                    const description = metaByName("description") || metaByProp("og:description");
                    const title = metaByProp("og:title") || document.title || null;

                    return {
                        title,
                        description,
                        image_url: metaByProp("og:image"),
                        video_url: metaByProp("og:video"),
                        mp3_url: metaByProp("og:audio"),
                        final_url: window.location.href,
                    };
                }"""
            )

            title = payload.get("title")
            artist_display = None
            artist_username = None
            description = payload.get("description")
            if description and " by " in description:
                left, right = description.split(" by ", 1)
                title = title or left.strip() or None
                artist_display = right.strip() or None

            return SunoTrackData(
                suno_url=payload.get("final_url") or suno_url,
                title=title,
                artist_display=artist_display,
                artist_username=artist_username,
                lyrics=None,
                image_url=payload.get("image_url"),
                video_url=payload.get("video_url"),
                mp3_url=payload.get("mp3_url"),
            )
        finally:
            await browser.close()


class FallbackSunoClient(SunoClient):
    """
    Composite Suno client.

    Strategy:
    1) Fetch via primary Httpx client.
    2) Validate required/quality fields.
    3) On failure or incompleteness, fetch via fallback browser scraper.
    4) Merge deterministically: keep primary non-empty values, fill gaps from fallback.
    """

    def __init__(
        self,
        *,
        primary_client: SunoClient | None = None,
        fallback_client: SunoClient | None = None,
        quality_fields: Iterable[str] = _QUALITY_FIELDS,
    ) -> None:
        self._primary = primary_client or HttpxSunoClient()
        self._fallback = fallback_client or PyppeteerSunoClient()
        self._quality_fields = tuple(quality_fields)

    async def fetch_track(self, suno_url: str) -> SunoTrackData:
        primary_data: SunoTrackData | None = None
        primary_error: Exception | None = None

        try:
            primary_data = await self._primary.fetch_track(suno_url)
        except Exception as exc:  # pragma: no cover - tested via behavior
            primary_error = exc

        needs_fallback = (
            primary_data is None
            or self._missing_fields(primary_data, _REQUIRED_FIELDS)
            or self._missing_fields(primary_data, self._quality_fields)
        )

        if not needs_fallback:
            return primary_data

        fallback_data: SunoTrackData | None = None
        fallback_error: Exception | None = None
        try:
            fallback_data = await self._fallback.fetch_track(suno_url)
        except Exception as exc:  # pragma: no cover - tested via behavior
            fallback_error = exc

        if primary_data is None and fallback_data is None:
            if primary_error is not None:
                raise SunoScrapeError(
                    f"Primary Suno scrape failed: {primary_error!r}"
                ) from primary_error
            if fallback_error is not None:
                raise SunoScrapeError(
                    f"Fallback Suno scrape failed: {fallback_error!r}"
                ) from fallback_error
            raise SunoScrapeError(
                "Both primary and fallback Suno scrapers returned no data"
            )

        if primary_data is None:
            _LOG.info(
                "suno_fetch_fallback_used reason=primary_failure url=%s", suno_url
            )
            assert fallback_data is not None
            return fallback_data

        if fallback_data is None:
            _LOG.warning(
                "suno_fetch_fallback_failed reason=primary_incomplete url=%s primary_missing=%s fallback_error=%r",
                suno_url,
                self._missing_field_names(
                    primary_data, _REQUIRED_FIELDS + self._quality_fields
                ),
                fallback_error,
            )
            return primary_data

        merged = self._merge(primary_data, fallback_data)
        _LOG.info(
            "suno_fetch_fallback_used reason=primary_incomplete url=%s primary_missing=%s",
            suno_url,
            self._missing_field_names(
                primary_data, _REQUIRED_FIELDS + self._quality_fields
            ),
        )
        return merged

    @staticmethod
    def _missing_fields(track: SunoTrackData, fields: Iterable[str]) -> bool:
        return any(not getattr(track, field, None) for field in fields)

    @staticmethod
    def _missing_field_names(track: SunoTrackData, fields: Iterable[str]) -> list[str]:
        return [field for field in fields if not getattr(track, field, None)]

    @staticmethod
    def _merge(primary: SunoTrackData, fallback: SunoTrackData) -> SunoTrackData:
        return SunoTrackData(
            suno_url=primary.suno_url or fallback.suno_url,
            title=primary.title or fallback.title,
            artist_display=primary.artist_display or fallback.artist_display,
            artist_username=primary.artist_username or fallback.artist_username,
            lyrics=primary.lyrics or fallback.lyrics,
            image_url=primary.image_url or fallback.image_url,
            video_url=primary.video_url or fallback.video_url,
            mp3_url=primary.mp3_url or fallback.mp3_url,
        )
