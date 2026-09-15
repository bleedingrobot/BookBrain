"""AcquisitionProvider for Libgen (the libgen.li-family mirrors).

No official public search API — same scrape-based shape as Anna's Archive:
``index.php?req=<query>`` for search, ``ads.php?md5=<hash>`` for a detail
page, ``get.php?md5=<hash>&key=<key>`` for the keyed download link. Unlike
Anna's Archive, none of this sits behind Cloudflare/DDoS-Guard on any mirror
confirmed live as of 2026-09-16 — a direct httpx request is always enough,
no FlareSolverr fallback needed here.

Individual Libgen mirrors rot faster than the domain family as a whole (the
lesson from Anna's Archive's single-base_url fragility), so this provider
takes an ordered list of mirrors and tries each in turn for both search and
download, rather than one configurable base_url. This mirrors the approach
of calibrain/calibre-web-automated-book-downloader ("Shelfmark"), a mature
reference implementation of the same aggregation this codebase is building
natively.

Same failure philosophy as Anna's Archive: search() degrades quietly (any
failure -> []), never raises. download() raises AcquisitionUnavailable once
every mirror is exhausted.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
import time
from pathlib import Path

import httpx
from bs4 import BeautifulSoup, Tag

from app.core.config import get_settings
from app.providers.acquisition.base import AcquisitionProvider
from app.providers.acquisition.exceptions import AcquisitionUnavailable
from app.providers.acquisition.types import AcquisitionResult

logger = logging.getLogger(__name__)

_MIN_REQUEST_INTERVAL = 3.5  # seconds — same politeness floor as Anna's Archive

# Live-confirmed necessary 2026-09-16: at least one mirror (libgen.li) routes
# httpx's default "python-httpx/x.x" User-Agent to a stub "Welcome to nginx!"
# placeholder page instead of the real site (a reverse-proxy vhost rule, not
# a Cloudflare/DDoS-Guard challenge — no bypass infra needed, just a normal
# browser-shaped UA).
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)

_RESULTS_TABLE_ID = "tablelibgen"

# Files under this size on a "successful" download are almost certainly an
# error/interstitial page rather than a book — same threshold Shelfmark uses.
_MIN_VALID_FILE_SIZE = 10 * 1024

# Patterns for the keyed GET link on an ads.php page. Ported from Shelfmark's
# proven pattern list (release_sources/libgen/scraper.py) — the "GET" button
# markup varies slightly across mirror flavors, so several patterns are
# tried in order rather than betting on one shape.
_GET_KEY_PATTERNS = [
    re.compile(
        r'<a\s+href=["\']([^"\']*get\.php\?md5=[^"\']+&key=[^"\']+)["\'][^>]*>\s*'
        r"<h2[^>]*>GET</h2>\s*</a>",
        re.IGNORECASE,
    ),
    re.compile(
        r'<a[^>]+href=["\']([^"\']*get\.php\?md5=[^"\']+&(?:amp;)?key=[^"\']+)["\']',
        re.IGNORECASE,
    ),
    re.compile(
        r'href=["\']([^"\']*get\.php\?[^"\']*md5=[^"\']*&[^"\']*key=[^"\']+)["\']',
        re.IGNORECASE,
    ),
]


class LibgenProvider(AcquisitionProvider):
    name = "libgen"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(
            timeout=15.0, follow_redirects=True, headers={"User-Agent": _USER_AGENT}
        )
        self._rate_lock = asyncio.Lock()
        self._last_request: float = 0.0

    def is_enabled(self) -> bool:
        return get_settings().libgen_enabled

    def _mirrors(self) -> list[str]:
        raw = get_settings().libgen_base_urls
        return [url.strip().rstrip("/") for url in raw.split(",") if url.strip()]

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
        for base in self._mirrors():
            try:
                response = await self._throttled_get(f"{base}/index.php", params={"req": query})
            except httpx.HTTPError as exc:
                logger.warning("libgen: search request to %s failed: %s", base, exc)
                continue
            if response.status_code != 200:
                continue
            results = self._parse_search_results(response.text, base)
            if results is not None:
                return results
        return []

    def _parse_search_results(self, html: str, base: str) -> list[AcquisitionResult] | None:
        """None when the page has no results table (caller tries the next
        mirror); a list (possibly empty) when the table is present.

        Row shapes carry no rowspans: full rows have 9 cells
        [Title, Author, Publisher, Year, Language, Pages, Size, Ext, Mirrors]
        and compact rows (extra files under one edition) have 5
        [Title, Pages, Size, Ext, Mirrors]. The file-level columns are stable
        from the right, so index from the end.
        """
        soup = BeautifulSoup(html, "html.parser")
        table = soup.find("table", id=_RESULTS_TABLE_ID)
        if not isinstance(table, Tag):
            return None

        results: list[AcquisitionResult] = []
        for row in table.find_all("tr")[1:]:  # skip the header row
            cells = row.find_all("td")
            if len(cells) < 5:
                continue
            # Scope the md5 to the Mirrors cell (last column) — scanning the
            # whole row risks matching an md5-shaped string elsewhere (e.g. a
            # cover-image URL) and misattributing it.
            md5_match = re.search(r"md5=([0-9a-f]{32})", str(cells[-1]), re.IGNORECASE)
            if not md5_match:
                continue
            md5 = md5_match.group(1).lower()

            fmt = self._cell_text(cells[-2]).lower() or "epub"
            if fmt != "epub":
                # EPUB only, by design — same rule acquisition_service.py
                # enforces for candidate matching, and what AnnasArchiveProvider
                # gets for free via its search's own ext=epub query param.
                # Libgen's search has no server-side format filter, so unlike
                # Anna's Archive this is applied client-side after parsing.
                continue

            title = self._title_text(cells[0])
            if not title:
                continue
            size = self._cell_text(cells[-3])
            author = self._cell_text(cells[1]) if len(cells) >= 9 else ""

            results.append(
                AcquisitionResult(
                    server=None,
                    author=author,
                    title=title,
                    format=fmt,
                    size=size,
                    full=md5,
                    provider=self.name,
                )
            )
        return results

    @staticmethod
    def _cell_text(cell: Tag) -> str:
        return re.sub(r"\s+", " ", cell.get_text(" ", strip=True)).strip()

    @classmethod
    def _title_text(cls, cell: Tag) -> str:
        """Title cell text with the view/favorite/read-count badges dropped.

        The real Title cell nests a series link, the edition title, an ISBN
        list, and — only for this cell — a run of ``<span class="badge...">``
        counters (e.g. "b l 508418 f 467902 r 103801") that carry no title
        signal and would otherwise get glued onto the end of every title.
        Everything else is left in place: noisy, but still book-identifying
        text, and downstream matching (acquisition_service's fuzzy
        title_similarity) already tolerates a verbose haystack — same
        rationale AnnasArchiveProvider uses for its own imperfect titles.
        """
        for badge in cell.select('span[class*="badge"]'):
            badge.decompose()
        return cls._cell_text(cell)

    async def download(self, handle: str) -> Path:
        md5 = handle.lower()
        for base in self._mirrors():
            ads_url = f"{base}/ads.php?md5={md5}"
            try:
                ads_response = await self._throttled_get(ads_url)
            except httpx.HTTPError as exc:
                logger.warning("libgen: detail request to %s failed: %s", ads_url, exc)
                continue
            if ads_response.status_code != 200:
                continue

            get_url = self._resolve_download_url(ads_response.text, base)
            if get_url is None:
                continue

            data = await self._try_download(get_url)
            if data is not None:
                # Generic suffix: unlike Anna's Archive (epub-only search),
                # Libgen legitimately returns pdf/mobi/cbz/fb2 too, and the
                # real format/filename travels separately via the
                # AcquisitionResult the caller already picked — this tempfile
                # is scratch space, not the final filename (acquire_service
                # uploads it under the caller-supplied name, not this one).
                fd, tmp_path = tempfile.mkstemp(suffix=".bin", prefix="libgen_")
                os.close(fd)
                Path(tmp_path).write_bytes(data)
                return Path(tmp_path)

        raise AcquisitionUnavailable("no usable Libgen mirror — all mirrors failed or were exhausted")

    @staticmethod
    def _resolve_download_url(ads_html: str, base: str) -> str | None:
        if "get.php" not in ads_html:
            return None
        for pattern in _GET_KEY_PATTERNS:
            match = pattern.search(ads_html)
            if not match:
                continue
            url = match.group(1).replace("&amp;", "&")
            if not url.startswith("http"):
                url = f"{base}/{url.lstrip('/')}"
            return url
        return None

    async def _try_download(self, url: str) -> bytes | None:
        try:
            response = await self._throttled_get(url)
        except httpx.HTTPError:
            return None
        if response.status_code != 200 or len(response.content) < _MIN_VALID_FILE_SIZE:
            return None
        return response.content
