"""Client for an optional FlareSolverr sidecar (github.com/FlareSolverr/FlareSolverr,
run via backend/tools/run-flaresolverr.sh) — solves Cloudflare/DDoS-Guard
challenges by driving a real headless-adjacent browser, so a scraping
AcquisitionProvider (currently just Anna's Archive) doesn't need its own
browser-automation dependency.

Generic, not Anna's-Archive-specific — any future scrape-based provider can
reuse this unchanged.

FlareSolverr itself has a known false-positive bug (PR #1773, unmerged as of
2026-09): when DDoS-Guard escalates to a manual CAPTCHA, a case-sensitivity
bug in FlareSolverr's own challenge-detection can report `"status": "ok"` on
what's actually still a CAPTCHA page. `solve()` does its own sanity check on
the returned HTML rather than trusting `status: ok` alone.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

# Title/body text of a still-unsolved challenge page, lowercase-matched (which
# incidentally sidesteps the exact case-sensitivity bug FlareSolverr's own
# detector has for "DDoS-Guard" vs "DDOS-GUARD"). Shared with annas_archive.py
# so both the direct-fetch path and the FlareSolverr-fallback path agree on
# what "still blocked" looks like.
_CHALLENGE_MARKERS = (
    "cf-browser-verification",
    "cf_chl_",
    "just a moment...",
    "checking your browser",
    "ddos-guard",
)


def looks_like_challenge_page(html: str, status_code: int) -> bool:
    if status_code in (403, 429, 503):
        return True
    lowered = (html or "").lower()
    return any(marker in lowered for marker in _CHALLENGE_MARKERS)


@dataclass
class SolvedPage:
    html: str
    status: int
    cookies: dict[str, str]
    user_agent: str | None


class FlareSolverrClient:
    """`base_url` is the full `/v1` endpoint, e.g. `http://127.0.0.1:8191/v1`."""

    def __init__(self, base_url: str, client: httpx.AsyncClient | None = None) -> None:
        self._url = base_url
        # FlareSolverr's own maxTimeout is per-request (default below); the
        # httpx client timeout just needs enough slack on top of that.
        self._client = client or httpx.AsyncClient(timeout=70.0)

    async def _post(self, payload: dict) -> dict | None:
        try:
            response = await self._client.post(self._url, json=payload)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("flaresolverr: request failed (%s): %s", payload.get("cmd"), exc)
            return None
        try:
            return response.json()
        except ValueError:
            logger.warning("flaresolverr: non-JSON response for %s", payload.get("cmd"))
            return None

    async def solve(
        self, url: str, *, session: str | None = None, max_timeout_ms: int = 60000
    ) -> SolvedPage | None:
        payload = {"cmd": "request.get", "url": url, "maxTimeout": max_timeout_ms}
        if session:
            payload["session"] = session
        body = await self._post(payload)
        if body is None or body.get("status") != "ok":
            return None

        solution = body.get("solution") or {}
        html = solution.get("response") or ""
        status = int(solution.get("status") or 0)
        if looks_like_challenge_page(html, status):
            logger.warning(
                "flaresolverr: reported 'ok' for %s but the page still looks like a "
                "challenge (working around FlareSolverr's own PR #1773 false-positive)",
                url,
            )
            return None

        cookies = {
            c["name"]: c["value"]
            for c in (solution.get("cookies") or [])
            if isinstance(c, dict) and c.get("name")
        }
        return SolvedPage(html=html, status=status, cookies=cookies, user_agent=solution.get("userAgent"))

    async def create_session(self) -> str | None:
        body = await self._post({"cmd": "sessions.create"})
        if body is None or body.get("status") != "ok":
            return None
        return (body.get("solution") or {}).get("session")

    async def destroy_session(self, session_id: str) -> None:
        try:
            await self._post({"cmd": "sessions.destroy", "session": session_id})
        except Exception:  # noqa: BLE001 - best effort cleanup
            logger.exception("flaresolverr: couldn't destroy session %s", session_id)
