"""prompts/27 Part 2 — fill each `Author` row's `hardcover_json` with
Hardcover's recent + near-future books for that author, so the library-viewer
can show a "From authors you read" release strip that feeds the wishlist.

Series data (hardcover_series_service) only covers series; most of the library
is standalones and author-led. This is the per-author complement.

Same shape as the other Hardcover refresh services: capped + incremental,
runs in the nightly job + `POST /api/library/new-releases/refresh`. One
GraphQL call per author. Every failure is swallowed — an author just keeps
their old data (or none) and is retried next run. Rate-limit-aware: a daily
429 stops the run cleanly, keeping everything already committed.
"""

import logging
from datetime import UTC, date, datetime, timedelta

import httpx
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.data.models import Author, Book, File, FileStatus
from app.providers.metadata.hardcover import (
    HardcoverRateLimited,
    HardcoverUnavailable,
    _TokenBucket,
    hardcover_graphql,
)
from app.services.hardcover_recs_service import _BOOK_CATEGORY, _tag_names
from app.services.text_match import normalize_title

logger = logging.getLogger(__name__)

_KEEP = 12  # books stored per author
_FAR_FUTURE_YEARS = 3
_PLACEHOLDER_TITLE = ("untitled",)
_WINDOW_MONTHS = 4  # $since = today − this many months

# `books` filtered to this author's canonical, non-compilation, non-partial
# works released within the window or announced for the future. The
# `contributions.author.name` filter is what actually resolves the author
# (the `authors { contributions(...) }` shape returns [] — validated live
# 2026-09-08). `canonical_id: {_is_null: true}` collapses translations.
_AUTHOR_BOOKS = """
query BookBrainAuthorReleases($name: String!, $since: date!) {
  books(
    where: {
      contributions: {author: {name: {_eq: $name}}}
      release_date: {_gte: $since}
      compilation: {_eq: false}
      is_partial_book: {_eq: false}
      canonical_id: {_is_null: true}
    }
    order_by: {users_count: desc}
    limit: 20
  ) {
    title
    release_date
    book_category_id
    editions(where: {isbn_13: {_is_null: false}}, limit: 1, order_by: {users_count: desc}) {
      isbn_13
    }
    cached_tags
  }
}
"""


def real_release_date(title: str, release_date: object) -> date | None:
    """Part A's `realReleaseDate` rule, backend side: an "Untitled …" title or
    a date more than `_FAR_FUTURE_YEARS` out is a placeholder, not a real
    announcement. Returns the parsed date (past or future) or None."""
    if not isinstance(title, str) or title.strip().lower().startswith(_PLACEHOLDER_TITLE):
        return None
    if not isinstance(release_date, str) or not release_date.strip():
        return None
    try:
        d = date.fromisoformat(release_date.strip()[:10])
    except ValueError:
        return None
    cutoff = date.today().replace(year=date.today().year + _FAR_FUTURE_YEARS)
    return None if d > cutoff else d


def _map_book(row: dict) -> dict | None:
    title = row.get("title")
    d = real_release_date(title, row.get("release_date"))
    if d is None:
        return None
    editions = row.get("editions") or []
    isbn13 = editions[0].get("isbn_13") if editions and isinstance(editions[0], dict) else None
    genres = _tag_names(row.get("cached_tags"), "Genre")
    out = {"title": title.strip(), "releaseDate": d.isoformat()}
    if isinstance(isbn13, str) and isbn13.strip():
        out["isbn13"] = isbn13.strip()
    category = _BOOK_CATEGORY.get(row.get("book_category_id"))
    if category and category != "Book":
        out["category"] = category
    if genres:
        out["genres"] = genres
    return out


async def _author_books(
    client: httpx.AsyncClient, token: str, bucket: _TokenBucket, name: str, since: date
) -> list[dict] | None:
    data = await hardcover_graphql(
        client, token, _AUTHOR_BOOKS, {"name": name, "since": since.isoformat()}, bucket
    )
    if data is None:
        raise HardcoverUnavailable  # call failed — don't wipe existing data
    rows = data.get("books") or []
    books: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        mapped = _map_book(row) if isinstance(row, dict) else None
        if mapped is None:
            continue
        key = normalize_title(mapped["title"])
        if key in seen:
            continue
        seen.add(key)
        books.append(mapped)
        if len(books) >= _KEEP:
            break
    return books


async def refresh_new_releases(
    session: AsyncSession,
    *,
    limit: int = 120,
    stale_after_days: int = 14,
    client: httpx.AsyncClient | None = None,
) -> dict:
    settings = get_settings()
    token = (settings.hardcover_api_token or "").strip()
    if not token:
        return {"skipped": "no HARDCOVER_API_TOKEN"}

    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=stale_after_days)
    authors = (
        (
            await session.execute(
                select(Author)
                .join(Book, Book.author_id == Author.id)
                .join(File, File.book_id == Book.id)
                .where(File.status == FileStatus.organised)
                .where(
                    or_(
                        Author.hardcover_synced_at.is_(None),
                        Author.hardcover_synced_at < cutoff,
                    )
                )
                .distinct()
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )

    since = date.today().replace(day=1)
    month = since.month - _WINDOW_MONTHS
    year = since.year
    while month <= 0:
        month += 12
        year -= 1
    since = date(year, month, 1)

    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=12.0)
    bucket = _TokenBucket(rate_per_sec=0.9, burst=8)
    counts = {"authors": 0, "with_books": 0, "empty": 0, "failed": 0}
    stopped_early = False

    try:
        for author in authors:
            try:
                books = await _author_books(http, token, bucket, author.name, since)
                author.hardcover_json = {"books": books or []}
                author.hardcover_synced_at = datetime.now(UTC).replace(tzinfo=None)
                counts["authors"] += 1
                counts["with_books" if books else "empty"] += 1
            except HardcoverRateLimited:
                stopped_early = True
                break
            except HardcoverUnavailable:
                counts["failed"] += 1
                await session.rollback()
            except Exception:  # noqa: BLE001 — one bad author must not stop the run
                logger.exception("hardcover new releases failed for %r", author.name)
                counts["failed"] += 1
                await session.rollback()
            finally:
                try:
                    await session.commit()
                except Exception:  # noqa: BLE001
                    logger.exception("hardcover new releases: per-author commit failed")
                    await session.rollback()
    finally:
        if owns_client:
            await http.aclose()

    if stopped_early:
        counts["rate_limited"] = True
    logger.info("hardcover new releases: %s", counts)
    return counts
