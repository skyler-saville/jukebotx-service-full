from __future__ import annotations

from dataclasses import dataclass
import logging


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SunoMediaMetadata:
    image_url: str | None
    video_url: str | None


class BrowserSunoMediaClient:
    """High-fidelity Suno media scraper using a headless browser runtime."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 25.0,
        user_agent: str = "jukebotx-media-worker/1.0",
    ) -> None:
        self._timeout_ms = int(timeout_seconds * 1000)
        self._user_agent = user_agent
        self._playwright = None
        self._browser = None

    async def start(self) -> None:
        try:
            from playwright.async_api import async_playwright
        except Exception as exc:
            raise RuntimeError(
                "Playwright is not installed. Install with 'poetry add playwright' and run "
                "'poetry run playwright install chromium'."
            ) from exc

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )

    async def close(self) -> None:
        browser = self._browser
        playwright = self._playwright
        self._browser = None
        self._playwright = None

        if browser is not None:
            await browser.close()
        if playwright is not None:
            await playwright.stop()

    async def fetch_media(self, url: str) -> SunoMediaMetadata:
        if self._browser is None:
            raise RuntimeError("BrowserSunoMediaClient is not started.")

        page = await self._browser.new_page(user_agent=self._user_agent)
        try:
            await page.goto(url, wait_until="networkidle", timeout=self._timeout_ms)
            payload = await page.evaluate(
                """
                () => {
                    const readMeta = (name, attr = 'property') => {
                        const tag = document.querySelector(`meta[${attr}="${name}"]`);
                        return tag ? tag.getAttribute('content') : null;
                    };

                    const videoNode = document.querySelector('video source, video');
                    const videoSrc = videoNode
                        ? (videoNode.getAttribute('src') || videoNode.currentSrc || null)
                        : null;

                    return {
                        image: readMeta('og:image') || readMeta('twitter:image', 'name'),
                        video: readMeta('og:video') || readMeta('og:video:url') || videoSrc,
                    };
                }
                """
            )
            if not isinstance(payload, dict):
                return SunoMediaMetadata(image_url=None, video_url=None)

            image_url = payload.get("image")
            video_url = payload.get("video")
            if image_url and isinstance(image_url, str):
                image_url = image_url.strip() or None
            else:
                image_url = None
            if video_url and isinstance(video_url, str):
                video_url = video_url.strip() or None
            else:
                video_url = None

            return SunoMediaMetadata(image_url=image_url, video_url=video_url)
        finally:
            await page.close()
