"""AcquisitionProvider for Anna's Archive (https://annas-archive.org).

No official public search API — results come from scraping the search page's
HTML, and a download is a chain: search result -> detail page (`/md5/<hash>`)
-> a list of mirror links, tried in order.

Search and detail pages, and some download mirrors, can sit behind a
Cloudflare or DDoS-Guard challenge. A direct httpx request is always tried
first (cheap, works for an unprotected mirror); if that's blocked and a
FlareSolverr sidecar is configured (ANNAS_ARCHIVE_FLARESOLVERR_URL — see
backend/tools/run-flaresolverr.sh), the page is solved through it instead.
Without FlareSolverr configured, a blocked page is skipped exactly like
before — this provider never requires it.

Same failure philosophy as the metadata providers (Google Books, Hardcover):
search() degrades quietly (any failure -> []), never raises. download() is
allowed to raise AcquisitionUnavailable once every option is exhausted —
that's actionable for whoever clicked "Get this" (try the next alternative),
unlike a routine single-mirror hiccup during search.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from app.core.config import get_settings
from app.providers.acquisition.base import AcquisitionProvider
from app.providers.acquisition.exceptions import AcquisitionUnavailable
from app.providers.acquisition.flaresolverr import FlareSolverrClient, looks_like_challenge_page
from app.providers.acquisition.types import AcquisitionResult

logger = logging.getLogger(__name__)

_MIN_REQUEST_INTERVAL = 3.5  # seconds — politeness floor toward a scraped site with no formal API


@dataclass
class _Page:
    html: str
    cookies: dict[str, str]
    user_agent: str | None


class AnnasArchiveProvider(AcquisitionProvider):
    name = "annas_archive"

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        flaresolverr: FlareSolverrClient | None = None,
    ) -> None:
        self._client = client or httpx.AsyncClient(timeout=15.0, follow_redirects=True)
        self._rate_lock = asyncio.Lock()
        self._last_request: float = 0.0
        if flaresolverr is not None:
            self._flaresolverr = flaresolverr
        else:
            url = get_settings().annas_archive_flaresolverr_url
            self._flaresolverr = FlareSolverrClient(url) if url else None

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

    async def _fetch_page(
        self, url: str, *, params: dict | None = None, session: str | None = None
    ) -> _Page | None:
        """Direct httpx first; falls back to FlareSolverr (if configured) when
        the direct response is an error or looks like an unsolved challenge.
        None if both fail, or if blocked with no FlareSolverr configured."""
        response: httpx.Response | None = None
        try:
            response = await self._throttled_get(url, params=params)
        except httpx.HTTPError as exc:
            logger.warning("annas_archive: request to %s failed: %s", url, exc)

        if (
            response is not None
            and response.status_code < 400
            and not looks_like_challenge_page(response.text, response.status_code)
        ):
            return _Page(html=response.text, cookies={}, user_agent=None)

        if self._flaresolverr is None:
            return None
        solved = await self._flaresolverr.solve(url, session=session)
        if solved is None:
            return None
        return _Page(html=solved.html, cookies=solved.cookies, user_agent=solved.user_agent)

    async def search(self, query: str) -> list[AcquisitionResult]:
        base = get_settings().annas_archive_base_url.rstrip("/")
        page = await self._fetch_page(f"{base}/search", params={"q": query, "ext": "epub"})
        if page is None:
            return []
        return self._parse_search_results(page.html, base)

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

        session_id = await self._flaresolverr.create_session() if self._flaresolverr else None
        try:
            page = await self._fetch_page(detail_url, session=session_id)
            if page is None:
                raise AcquisitionUnavailable(
                    f"couldn't load the Anna's Archive detail page (blocked, and no bypass available): {detail_url}"
                )

            for mirror_url in self._mirror_links(page.html, base):
                data = await self._try_mirror(mirror_url, cookies=page.cookies, user_agent=page.user_agent)
                if data is not None:
                    fd, tmp_path = tempfile.mkstemp(suffix=".epub", prefix="annas_archive_")
                    os.close(fd)
                    Path(tmp_path).write_bytes(data)
                    return Path(tmp_path)

            raise AcquisitionUnavailable(
                "no usable mirror — all links failed or are Cloudflare/DDoS-Guard-protected"
            )
        finally:
            if self._flaresolverr is not None and session_id:
                await self._flaresolverr.destroy_session(session_id)

    def _mirror_links(self, html: str, base: str) -> list[str]:
        soup = BeautifulSoup(html, "html.parser")
        links: list[str] = []
        for a in soup.select("a[href]"):
            href = a["href"]
            if any(marker in href for marker in ("/slow_download/", "/fast_download/", "/dyn/")):
                links.append(urljoin(base + "/", href))
        return links

    async def _try_mirror(
        self, url: str, *, cookies: dict[str, str] | None = None, user_agent: str | None = None
    ) -> bytes | None:
        # Replay whatever cookies/UA solved the detail page — the documented
        # FlareSolverr pattern: it can't proxy binary content itself, so a
        # solved challenge's cookies are handed to a plain httpx request
        # instead. A mismatched User-Agent invalidates the cookie, so both
        # travel together or not at all. Built as a raw Cookie header, not
        # httpx's per-request `cookies=` kwarg — that's deprecated in favour
        # of setting cookies on the client instance, which would leak this
        # one-off cookie jar into every other request the shared client makes.
        headers: dict[str, str] = {}
        if user_agent:
            headers["User-Agent"] = user_agent
        if cookies:
            headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
        try:
            response = await self._throttled_get(url, headers=headers or None)
        except httpx.HTTPError:
            return None
        if looks_like_challenge_page(response.text, response.status_code):
            logger.debug("annas_archive: mirror %s looks challenge-protected, skipping", url)
            return None
        if response.status_code != 200 or not response.content:
            return None
        return response.content
