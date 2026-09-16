"""prompts/25 Phase 2 — fill in each `Series` row's `hardcover_json` with
Hardcover's canonical view of that series (its ordered book list), so the
library-viewer can show *named* missing entries instead of guessing from
gaps in what's owned.

Runs in the nightly job and via `POST /api/library/series-catalog/refresh`.
Incremental + capped: only series with an organised book and a stale/absent
sync are touched, `limit` per run, so it converges over a few nights and
stays well under Hardcover's 5,000/day free cap. Every failure is swallowed
— a series just keeps its old data (or none) and gets retried next run.

Hardcover's series data is good but not pristine: overlapping series ("The
Mistborn Saga" vs "…The Original Trilogy"), novella positions like 3.5, the
odd foreign-language title in a slot. So the match is best-effort, stored
with the Hardcover name + slug so a bad match is visible in the viewer, and
`match: "manual"` lets James pin `hardcover_json.id` by hand (never
re-matched, list still refreshed).
"""

import logging
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.data.models import Author, Book, File, FileStatus, Series
from app.providers.metadata.hardcover import (
    HardcoverRateLimited,
    HardcoverUnavailable,
    _TokenBucket,
    hardcover_graphql,
)
from app.services.text_match import normalize_person_name, normalize_words

logger = logging.getLogger(__name__)

_ARTICLES = frozenset({"the", "a", "an"})
_MAX_ENTRIES = 60

_SERIES_SEARCH = """
query BookBrainSeriesSearch($q: String!) {
  search(query: $q, query_type: "Series", per_page: 5) {
    results
  }
}
"""

# The "Getting All Books in a Series" query from Hardcover's own guide:
# distinct_on position + the users_count tiebreak (without it you get a
# random, often foreign-language, edition's title in each slot), and the
# canonical/partial/compilation filters that hide merged + companion rows.
_SERIES_BOOKS = """
query BookBrainSeriesBooks($id: Int!) {
  series(where: {id: {_eq: $id}}) {
    id
    name
    slug
    primary_books_count
    book_series(
      distinct_on: position
      order_by: [{position: asc}, {book: {users_count: desc}}]
      where: {
        book: {canonical_id: {_is_null: true}, is_partial_book: {_eq: false}}
        compilation: {_eq: false}
      }
    ) {
      position
      book {
        title
        release_date
        editions(where: {isbn_13: {_is_null: false}}, limit: 1, order_by: {users_count: desc}) {
          isbn_13
        }
      }
    }
  }
}
"""


def _series_key(name: str | None) -> frozenset[str]:
    return normalize_words(name) - _ARTICLES


def _match_score(bb_key: frozenset[str], hc_key: frozenset[str]) -> float | None:
    """How well a Hardcover series name matches a BookBrain one. None = reject.
    A subset ("mistborn" ⊆ "mistborn saga") scores high; otherwise Jaccard
    must clear 0.5. Fewer extra Hardcover words is better, so a plain
    "The Mistborn Saga" beats "…: The Original Trilogy"."""
    if not bb_key or not hc_key:
        return None
    inter = len(bb_key & hc_key)
    if inter == 0:
        return None
    union = len(bb_key | hc_key)
    jaccard = inter / union
    subset = bb_key <= hc_key
    if not subset and jaccard < 0.5:
        return None
    extra = len(hc_key - bb_key)
    # subset bonus, then penalise each extra HC word a little
    return (1.0 if subset else jaccard) - 0.05 * extra


async def _series_author(session: AsyncSession, series: Series) -> str | None:
    row = await session.execute(
        select(Book.author_id)
        .where(Book.series_id == series.id, Book.author_id.is_not(None))
        .limit(1)
    )
    author_id = row.scalar_one_or_none()
    if author_id is None:
        return None
    author = await session.get(Author, author_id)
    return author.name if author else None


def _hits(data: dict | None) -> list[dict]:
    results = ((data or {}).get("search") or {}).get("results") or {}
    hits = results.get("hits") if isinstance(results, dict) else None
    return [h["document"] for h in (hits or []) if isinstance(h, dict) and isinstance(h.get("document"), dict)]


async def _find_series_id(
    client: httpx.AsyncClient, token: str, bucket: _TokenBucket, name: str, author: str | None
) -> tuple[int, str, str] | None:
    """Returns (hardcover_id, hardcover_name, hardcover_slug), or None when the
    search genuinely turned up no acceptable match. Raises HardcoverUnavailable
    if the *call itself* failed — the caller must then leave the series as-is,
    never record `match:"none"` off a failed request."""
    query = f"{name} {author}".strip() if author else name.strip()
    data = await hardcover_graphql(client, token, _SERIES_SEARCH, {"q": query}, bucket)
    if data is None:
        raise HardcoverUnavailable
    bb_key = _series_key(name)
    author_key = normalize_person_name(author) if author else None

    best: tuple[float, dict] | None = None
    for doc in _hits(data):
        hc_name = doc.get("name")
        if not isinstance(hc_name, str):
            continue
        if author_key:
            hc_author = doc.get("author_name") or ""
            if normalize_person_name(hc_author) != author_key:
                continue
        score = _match_score(bb_key, _series_key(hc_name))
        if score is None:
            continue
        if best is None or score > best[0]:
            best = (score, doc)

    if best is None:
        return None
    doc = best[1]
    try:
        hc_id = int(doc["id"])
    except (KeyError, TypeError, ValueError):
        return None
    return hc_id, doc["name"], doc.get("slug") or ""


async def _fetch_books(
    client: httpx.AsyncClient, token: str, bucket: _TokenBucket, hc_id: int
) -> tuple[list[dict], int | None, str, str] | None:
    """Returns (books, primary_count, name, slug) — books as
    [{position: float, title: str, releaseDate: str|None, isbn13: str|None}],
    numeric positions only, capped. `releaseDate` (prompts/26 Part A) drives the
    viewer's "next up" / "coming soon" split — it's an ISO date string or absent.
    `isbn13` (prompts/27 Part 1) lets the viewer pull an Open Library cover for a
    not-yet-owned entry — the most-popular edition's ISBN-13, or absent."""
    data = await hardcover_graphql(client, token, _SERIES_BOOKS, {"id": hc_id}, bucket)
    if data is None:
        raise HardcoverUnavailable
    rows = data.get("series") or []
    if not rows:
        return None
    s = rows[0]
    books: list[dict] = []
    for entry in s.get("book_series") or []:
        pos = entry.get("position")
        book = entry.get("book") or {}
        title = book.get("title")
        if not isinstance(title, str) or not title.strip():
            continue
        try:
            pos_f = float(pos)
        except (TypeError, ValueError):
            continue
        item = {"position": pos_f, "title": title.strip()}
        release_date = book.get("release_date")
        if isinstance(release_date, str) and release_date.strip():
            item["releaseDate"] = release_date.strip()
        editions = book.get("editions") or []
        isbn13 = editions[0].get("isbn_13") if editions and isinstance(editions[0], dict) else None
        if isinstance(isbn13, str) and isbn13.strip():
            item["isbn13"] = isbn13.strip()
        books.append(item)
        if len(books) >= _MAX_ENTRIES:
            break
    return books, s.get("primary_books_count"), s.get("name") or "", s.get("slug") or ""


async def refresh_series_catalog(
    session: AsyncSession,
    *,
    limit: int = 300,
    stale_after_days: int = 30,
    client: httpx.AsyncClient | None = None,
) -> dict:
    settings = get_settings()
    token = (settings.hardcover_api_token or "").strip()
    if not token:
        return {"skipped": "no HARDCOVER_API_TOKEN"}

    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=stale_after_days)
    stale = (
        (
            await session.execute(
                select(Series)
                .join(Book, Book.series_id == Series.id)
                .join(File, File.book_id == Book.id)
                .where(File.status == FileStatus.organised)
                .where(
                    or_(
                        Series.hardcover_synced_at.is_(None),
                        Series.hardcover_synced_at < cutoff,
                    )
                )
                .distinct()
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )

    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=12.0)
    bucket = _TokenBucket(rate_per_sec=0.9, burst=8)
    counts = {"matched": 0, "refreshed": 0, "unmatched": 0, "failed": 0}

    stopped_early = False
    try:
        for series in stale:
            existing = series.hardcover_json if isinstance(series.hardcover_json, dict) else {}
            try:
                if existing.get("match") == "manual" and existing.get("id"):
                    hc_id = int(existing["id"])
                    hc_name, hc_slug, match = existing.get("name", ""), existing.get("slug", ""), "manual"
                else:
                    author = await _series_author(session, series)
                    found = await _find_series_id(http, token, bucket, series.name, author)
                    if found is None:
                        series.hardcover_json = {"match": "none"}
                        series.hardcover_synced_at = datetime.now(UTC).replace(tzinfo=None)
                        counts["unmatched"] += 1
                        continue
                    hc_id, hc_name, hc_slug, match = *found, "auto"
                    counts["matched"] += 1

                fetched = await _fetch_books(http, token, bucket, hc_id)
                if fetched is None:
                    counts["failed"] += 1
                    continue
                books, primary_count, fetched_name, fetched_slug = fetched
                series.hardcover_json = {
                    "id": hc_id,
                    "name": hc_name or fetched_name,
                    "slug": hc_slug or fetched_slug,
                    "primaryCount": primary_count,
                    "books": books,
                    "match": match,
                }
                series.hardcover_synced_at = datetime.now(UTC).replace(tzinfo=None)
                counts["refreshed"] += 1
            except HardcoverRateLimited:
                # Daily quota gone — stop now, keep what's committed, come
                # back next run. Crucially: do NOT touch this series.
                stopped_early = True
                break
            except HardcoverUnavailable:
                # Transient call failure — leave this series' existing data
                # alone (a failed search must never become `match:"none"`).
                logger.info("hardcover series: skipped %r (call failed)", series.name)
                counts["failed"] += 1
                await session.rollback()
            except Exception:  # noqa: BLE001 — one bad series must not stop the run
                logger.exception("hardcover series refresh failed for %r", series.name)
                counts["failed"] += 1
                await session.rollback()
            finally:
                # Commit per series, not once at the end — each iteration does
                # ~2 slow HTTP calls, so a single transaction would hold the
                # SQLite write lock for minutes and time out anything else
                # (a Torrents scan, an organize) that needs to write meanwhile.
                try:
                    await session.commit()
                except Exception:  # noqa: BLE001
                    logger.exception("hardcover series: per-row commit failed")
                    await session.rollback()
    finally:
        if owns_client:
            await http.aclose()

    if stopped_early:
        counts["rate_limited"] = True
    logger.info("hardcover series catalog: %s", counts)
    return counts
