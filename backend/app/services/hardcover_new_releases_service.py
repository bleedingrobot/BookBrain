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

prompts/28 Phase 1 — the same per-author request also carries an `authors`
root field, so this pass resolves `Author.hardcover_person_id` (the canonical
+ alias walk) for free. `repair_forked_authors.py` then merges rows that
share a person id. Run `new-releases/refresh?stale_days=0` once to backfill.
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
from app.services.text_match import normalize_person_name, normalize_title

logger = logging.getLogger(__name__)

_KEEP = 12  # books stored per author
_FAR_FUTURE_YEARS = 3
_PLACEHOLDER_TITLE = ("untitled",)
_WINDOW_MONTHS = 4  # $since = today − this many months

# One request, two roots (Hardcover allows ≤5 top-level queries/request):
#
#   books  — this author's canonical, non-compilation, non-partial works in
#            the window or announced for the future. The
#            `contributions.author.name` filter is what resolves the author
#            (the `authors { contributions(...) }` shape returns [] — validated
#            live 2026-09-08). `canonical_id: {_is_null: true}` drops
#            translations.
#   authors — the same-named author row(s), plus the `canonical` (dedup) and
#            `alias` (pen name → real identity) hops, for prompts/28's person
#            id. Ordered by book count so row 0 is the real one.
_AUTHOR_INFO = """
query BookBrainAuthorInfo($name: String!, $since: date!) {
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
  authors(where: {name: {_eq: $name}}, order_by: {books_count: desc_nulls_last}, limit: 5) {
    id
    name
    alternate_names
    alias { id name }
    canonical { id name alias { id name } }
  }
}
"""


# prompts/27 Part 3 — Hardcover's most-wanted upcoming books overall. There's
# no queryable "official Most Anticipated list" (validated live: `_ilike` list
# search is blocked server-side, and there's no stable list slug to pin), so
# it's a raw `users_count` sort over future releases with the same placeholder
# filter as everything else.
_GLOBAL_ANTICIPATED = """
query BookBrainGlobalAnticipated($today: date!) {
  books(
    where: {
      release_date: {_gte: $today}
      compilation: {_eq: false}
      is_partial_book: {_eq: false}
      canonical_id: {_is_null: true}
    }
    order_by: {users_count: desc}
    limit: 60
  ) {
    title
    release_date
    book_category_id
    contributions(limit: 1) { author { name } }
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


def _map_book(row: dict, *, with_author: bool = False) -> dict | None:
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
    if with_author:
        contribs = row.get("contributions") or []
        author = contribs[0].get("author") if contribs and isinstance(contribs[0], dict) else None
        name = author.get("name") if isinstance(author, dict) else None
        if isinstance(name, str) and name.strip():
            out["author"] = name.strip()
    return out


async def fetch_global_anticipated(
    *, limit: int = 40, client: httpx.AsyncClient | None = None
) -> list[dict]:
    """prompts/27 Part 3 — the most-wanted upcoming books overall. One
    Hardcover call, best-effort: any failure (including a rate limit — it's a
    single non-critical call) returns []. Not filtered to the library; the
    viewer excludes what's already owned/wishlisted and only shows the strip
    when the opt-in setting is on."""
    settings = get_settings()
    token = (settings.hardcover_api_token or "").strip()
    if not token:
        return []
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=12.0)
    bucket = _TokenBucket(rate_per_sec=0.9, burst=8)
    try:
        data = await hardcover_graphql(
            http, token, _GLOBAL_ANTICIPATED, {"today": date.today().isoformat()}, bucket
        )
    except HardcoverRateLimited:
        return []
    except Exception:  # noqa: BLE001
        logger.exception("hardcover global anticipated fetch failed")
        return []
    finally:
        if owns_client:
            await http.aclose()

    out: list[dict] = []
    seen: set[str] = set()
    for row in (data or {}).get("books") or []:
        mapped = _map_book(row, with_author=True) if isinstance(row, dict) else None
        if mapped is None:
            continue
        key = normalize_title(mapped["title"])
        if key in seen:
            continue
        seen.add(key)
        out.append(mapped)
        if len(out) >= limit:
            break
    return out


def resolve_person_id(rows: object, bb_name: str) -> tuple[int, str] | None:
    """Walk Hardcover's author graph for `bb_name` to a stable "person id":
    the best-matching row (most books), then its `canonical` row (Hardcover's
    own dedup), then that row's `alias` (pen name → real identity).

    Guarded — returns None unless the matched row's `name` or one of its
    `alternate_names` normalises to `bb_name`, so a fuzzy Hardcover hit for a
    different person is never trusted.

    Verified live 2026-09-09: `Iain M. Banks` / `Iain Banks` → 95997;
    `Robert Galbraith` → 80626 (J.K. Rowling); `Richard A. Knaak` → 191045;
    a plain house pseudonym (`Richard Awlinson`) → its own id, no hops."""
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
        return None
    best = rows[0]  # ordered by books_count desc
    key = normalize_person_name(bb_name)
    if not key:
        return None
    names = [best.get("name"), *(best.get("alternate_names") or [])]
    if key not in {normalize_person_name(n) for n in names if isinstance(n, str)}:
        return None
    node = best.get("canonical") if isinstance(best.get("canonical"), dict) else best
    alias = node.get("alias") if isinstance(node.get("alias"), list) else []
    person = alias[0] if alias and isinstance(alias[0], dict) else node
    pid = person.get("id")
    if not isinstance(pid, int):
        return None
    name = person.get("name")
    return pid, name if isinstance(name, str) and name.strip() else str(best.get("name") or bb_name)


async def _author_info(
    client: httpx.AsyncClient, token: str, bucket: _TokenBucket, name: str, since: date
) -> tuple[list[dict], tuple[int, str] | None]:
    data = await hardcover_graphql(
        client, token, _AUTHOR_INFO, {"name": name, "since": since.isoformat()}, bucket
    )
    if data is None:
        raise HardcoverUnavailable  # call failed — don't wipe existing data
    books: list[dict] = []
    seen: set[str] = set()
    for row in data.get("books") or []:
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
    return books, resolve_person_id(data.get("authors"), name)


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
    counts = {"authors": 0, "with_books": 0, "empty": 0, "with_person_id": 0, "failed": 0}
    stopped_early = False

    try:
        for author in authors:
            try:
                books, person = await _author_info(http, token, bucket, author.name, since)
                author.hardcover_json = {"books": books or []}
                if person is not None:
                    author.hardcover_person_id = person[0]
                    counts["with_person_id"] += 1
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
