"""AcquisitionProvider for Anna's Archive (https://annas-archive.org).

No official public search API — results come from scraping the search page's
HTML, and a download is a chain: search result -> detail page (`/md5/<hash>`)
-> a list of mirror links, tried in order. Some mirrors sit behind a
Cloudflare challenge; this provider does NOT attempt to bypass Cloudflare
(no browser automation) — a blocked mirror is just skipped in favour of the
next one, and only once every mirror has failed does download() raise.

Same failure philosophy as the metadata providers (Google Books, Hardcover):
search() degrades quietly (httpx errors / no results -> []), never raises.
download() is allowed to raise AcquisitionUnavailable once every option is
exhausted — that's actionable for whoever clicked "Get this" (try the next
alternative), unlike a routine single-mirror hiccup during search.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
import time
from pathlib import Path
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from app.core.config import get_settings
from app.providers.acquisition.base import AcquisitionProvider
from app.providers.acquisition.exceptions import AcquisitionUnavailable
from app.providers.acquisition.types import AcquisitionResult

logger = logging.getLogger(__name__)

_MIN_REQUEST_INTERVAL = 3.5  # seconds — politeness floor toward a scraped site with no formal API

# Markers of a Cloudflare interstitial rather than the file itself — skip
# quietly and try the next mirror instead of treating it as a hard error.
_CLOUDFLARE_MARKERS = ("cf-browser-verification", "cf_chl_", "Just a moment...", "cloudflare")


class AnnasArchiveProvider(AcquisitionProvider):
    name = "annas_archive"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(timeout=15.0, follow_redirects=True)
        self._rate_lock = asyncio.Lock()
        self._last_request: float = 0.0

    def is_enabled(self) -> bool:
        return get_settings().annas_archive_enabled

    async def _throttled_get(self, url: str, **kwargs) -> httpx.Response:
        async with self._rate_lock:
            wait = _MIN_REQUEST_INTERVAL - (time.monotonic() - self._last_request)
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                return await self._client.get(url, **kwargs)
            finally:
                self._last_request = time.monotonic()

    async def search(self, query: str) -> list[AcquisitionResult]:
        base = get_settings().annas_archive_base_url.rstrip("/")
        try:
            response = await self._throttled_get(f"{base}/search", params={"q": query, "ext": "epub"})
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("annas_archive: search failed for %r: %s", query, exc)
            return []
        return self._parse_search_results(response.text, base)

    def _parse_search_results(self, html: str, base: str) -> list[AcquisitionResult]:
        soup = BeautifulSoup(html, "html.parser")
        results: list[AcquisitionResult] = []
        for link in soup.select('a[href*="/md5/"]'):
            href = link.get("href")
            if not href:
                continue
            handle = urljoin(base + "/", href.lstrip("/")).removeprefix(base).lstrip("/")
            text = " ".join(link.stripped_strings)
            if not text:
                continue
            title, author, fmt, size = self._parse_result_text(text)
            if not title:
                continue
            results.append(
                AcquisitionResult(
                    server=None,
                    author=author or "",
                    title=title,
                    format=fmt or "epub",
                    size=size or "",
                    full=handle,
                    provider=self.name,
                )
            )
        return results

    @staticmethod
    def _parse_result_text(text: str) -> tuple[str, str, str, str]:
        # Anna's Archive result cards read roughly
        # "Title — Author, Format, Size, ...". Best-effort split; a card that
        # doesn't match this shape still gets its whole text as the title
        # rather than being dropped, since score_candidate() fuzzy-matches
        # against the full haystack anyway.
        fmt_match = re.search(r"\b(epub|mobi|azw3|pdf|cbz|cbr)\b", text, re.IGNORECASE)
        size_match = re.search(r"([\d.]+\s?(?:KB|MB|GB))", text, re.IGNORECASE)
        fmt = fmt_match.group(1).lower() if fmt_match else ""
        size = size_match.group(1) if size_match else ""
        parts = re.split(r"[—\-]", text, maxsplit=1)
        title = parts[0].strip()
        author = parts[1].strip() if len(parts) > 1 else ""
        return title, author, fmt, size

    async def download(self, handle: str) -> Path:
        base = get_settings().annas_archive_base_url.rstrip("/")
        detail_url = f"{base}/{handle.lstrip('/')}"
        try:
            response = await self._throttled_get(detail_url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise AcquisitionUnavailable(f"couldn't load the Anna's Archive detail page: {exc}") from exc

        for mirror_url in self._mirror_links(response.text, base):
            data = await self._try_mirror(mirror_url)
            if data is not None:
                fd, tmp_path = tempfile.mkstemp(suffix=".epub", prefix="annas_archive_")
                os.close(fd)
                Path(tmp_path).write_bytes(data)
                return Path(tmp_path)

        raise AcquisitionUnavailable(
            "no usable mirror — all links failed or are Cloudflare-protected"
        )

    def _mirror_links(self, html: str, base: str) -> list[str]:
        soup = BeautifulSoup(html, "html.parser")
        links: list[str] = []
        for a in soup.select("a[href]"):
            href = a["href"]
            if any(marker in href for marker in ("/slow_download/", "/fast_download/", "/dyn/")):
                links.append(urljoin(base + "/", href))
        return links

    async def _try_mirror(self, url: str) -> bytes | None:
        try:
            response = await self._throttled_get(url)
        except httpx.HTTPError:
            return None
        if response.status_code == 403 or any(
            marker.lower() in response.text.lower() for marker in _CLOUDFLARE_MARKERS
        ):
            logger.debug("annas_archive: mirror %s looks Cloudflare-protected, skipping", url)
            return None
        if response.status_code != 200 or not response.content:
            return None
        return response.content
