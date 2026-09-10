"""prompts/32 — pull the latest articles from a curated set of SFF news /
review RSS+Atom feeds so the library-viewer can show a "From around the SFF
world" section under the release marquees.

The static viewer can't fetch these itself (no `Access-Control-Allow-Origin`
on any of them), so the backend does it and writes `bookbrain-news.json`,
same sidecar pattern as `bookbrain-new-releases.json`.

Only headline + short excerpt + a link back to the original is stored — RSS
is published for syndication and this is the standard, expected use. No full
article bodies, no proxying; the viewer always links out.

Best-effort throughout: one dead feed is logged and skipped, never fatal.
No API token, no Anthropic — just ~9 HTTP GETs per nightly run.
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
from datetime import UTC, datetime
from time import struct_time
from urllib.parse import urljoin

import feedparser
import httpx

logger = logging.getLogger(__name__)

# (display name, feed URL). RSS 2.0 unless noted. Curated for *books* —
# reviews, cover reveals, release round-ups, genre trade news. Reactor /
# File 770 / Swords & Spaceships were dropped 2026-09-10 (too much film / TV /
# fandom / newsletter-archive noise); the review blogs below came off
# feedspot.com/fantasy_book_rss_feeds and were each checked live.
_FEEDS: list[tuple[str, str]] = [
    ("Locus Online", "https://locusmag.com/feed/"),
    ("Grimdark Magazine", "https://www.grimdarkmagazine.com/feed/"),
    # Blogger Atom feed (the www.fantasybookcritic.com domain no longer resolves)
    ("Fantasy Book Critic", "https://fantasybookcritic.blogspot.com/feeds/posts/default"),
    ("The Fantasy Hive", "https://fantasy-hive.co.uk/feed/"),
    ("FanFiAddict", "https://fanfiaddict.com/feed/"),
    ("Book Riot SF/F", "https://bookriot.com/category/genre/science-fiction-fantasy/feed/"),
    ("Before We Go Blog", "https://beforewegoblog.com/feed/"),
    ("Fantasy Book Cafe", "https://fantasybookcafe.com/feed"),
    ("The Fantasy Inn", "https://thefantasyinn.com/feed"),
    ("Fantasy-Faction", "https://fantasy-faction.com/feed"),
    ("Pat's Fantasy Hotlist", "https://feeds.feedburner.com/PatsFantasyHotlist"),
]

_PER_FEED = 6  # newest entries kept per source
_SUMMARY_CAP = 280  # chars, after HTML strip
_TOTAL_CAP = 50
_TIMEOUT = 12.0
# Some WordPress / Cloudflare hosts 403 the default httpx UA.
_UA = "BookBrain/1.0 (+https://github.com/bleedingrobot/BookBrain) feed reader"

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
# WordPress feeds tack these onto summaries (and "The post…" onto titles too).
_WP_TRAILER_RES = (
    re.compile(r"\s*The post .+? appeared first on .+?\.?\s*$", re.IGNORECASE),
    re.compile(
        r"[\s…]*(?:\[…\]|\[\.\.\.\]|…)?\s*(?:\bThe\b\s*)?Continue reading\s*[→»\-]*\s*$",
        re.IGNORECASE,
    ),
    re.compile(r"\s*(?:Read more|Read the full (?:post|article|review))\s*[→»\-]*\s*$", re.IGNORECASE),
)


def _plain(raw: str | None) -> str:
    """Feed titles/summaries are HTML fragments with entities, sometimes
    double-escaped (`&lt;i&gt;`). Unescape first so those become real tags,
    strip tags, unescape once more for any bare `&amp;`, collapse whitespace."""
    if not raw:
        return ""
    text = html.unescape(raw)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text).strip()
    for pattern in _WP_TRAILER_RES:
        text = pattern.sub("", text)
    return text.strip()


def _truncate(text: str, cap: int) -> str:
    if len(text) <= cap:
        return text
    cut = text[:cap].rsplit(" ", 1)[0].rstrip(",.;:—- ")
    return f"{cut}…"


def _to_iso(parsed: struct_time | None) -> str | None:
    if not parsed:
        return None
    try:
        return datetime(*parsed[:6], tzinfo=UTC).isoformat()
    except (ValueError, TypeError):
        return None


def _entry_to_item(entry: object, source: str, feed_link: str) -> dict | None:
    title = _plain(getattr(entry, "title", "") or entry.get("title", ""))
    link = (getattr(entry, "link", "") or entry.get("link", "") or "").strip()
    if not title or not link:
        return None
    link = urljoin(feed_link or "", link)

    summary_raw = entry.get("summary") or ""
    if not summary_raw:
        content = entry.get("content") or []
        if content and isinstance(content, list) and isinstance(content[0], dict):
            summary_raw = content[0].get("value") or ""
    summary = _truncate(_plain(summary_raw), _SUMMARY_CAP)

    published = _to_iso(entry.get("published_parsed") or entry.get("updated_parsed"))

    return {
        "title": title,
        "link": link,
        "summary": summary,
        "published": published,
        "source": source,
    }


def _parse_feed(content: bytes, source: str) -> list[dict]:
    parsed = feedparser.parse(content)
    feed_link = ""
    feed = getattr(parsed, "feed", None)
    if feed is not None:
        feed_link = feed.get("link") or ""
    out: list[dict] = []
    for entry in (parsed.entries or [])[:_PER_FEED]:
        item = _entry_to_item(entry, source, feed_link)
        if item is not None:
            out.append(item)
    return out


async def _fetch_one(client: httpx.AsyncClient, name: str, url: str) -> list[dict]:
    try:
        resp = await client.get(url, headers={"User-Agent": _UA}, follow_redirects=True)
        resp.raise_for_status()
        return await asyncio.to_thread(_parse_feed, resp.content, name)
    except Exception:  # noqa: BLE001 — one bad feed must not sink the batch
        logger.warning("sff news: feed failed, skipping: %s (%s)", name, url, exc_info=True)
        return []


async def fetch_news(*, client: httpx.AsyncClient | None = None) -> list[dict]:
    """Every feed's newest few entries, merged, newest first, deduped by link,
    capped. Best-effort — returns whatever came back."""
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=_TIMEOUT)
    try:
        batches = await asyncio.gather(*(_fetch_one(http, n, u) for n, u in _FEEDS))
    finally:
        if owns_client:
            await http.aclose()

    seen: set[str] = set()
    items: list[dict] = []
    for batch in batches:
        for item in batch:
            if item["link"] in seen:
                continue
            seen.add(item["link"])
            items.append(item)

    items.sort(key=lambda i: i["published"] or "", reverse=True)
    logger.info("sff news: %d items from %d feeds", len(items), len(_FEEDS))
    return items[:_TOTAL_CAP]
