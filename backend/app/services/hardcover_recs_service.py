"""prompts/25 Phase 3 — fill each `Book` row's `hardcover_json` with
Hardcover's "readers also liked" list (from `books.cached_similar_book_ids`,
a precomputed similarity graph — plain catalogue data), so the library-viewer
can show a "Readers also liked" strip on a book that feeds straight into the
wishlist.

Same shape as hardcover_series_service: capped + incremental, runs in the
nightly job + `POST /api/library/book-recs/refresh`. Every failure is
swallowed — a book just keeps its old recs (or none) and is retried later.

Two Hardcover calls per book: one to turn the ISBN into a Hardcover book id
+ its similar-id list, one to resolve the top ids to title / author / ISBN.

prompts/26 Part B — the first call also pulls the book's own curated metadata
(rating, page count, category, genres, moods, content warnings — prompts/31
Part A) into `hardcover_json.meta`, so
the library-viewer can show badges and offer a genre facet. Part C adds
`meta.description` as a zero-cost source for fill-missing-descriptions.
"""

import logging
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.data.models import Book, File, FileStatus, Identifier, IdentifierType
from app.providers.metadata.hardcover import (
    HardcoverRateLimited,
    HardcoverUnavailable,
    _TokenBucket,
    hardcover_graphql,
)
from app.services.text_match import normalize_title

logger = logging.getLogger(__name__)

_KEEP = 15  # recs stored per book
_RESOLVE = 25  # top similar ids we bother resolving (some won't have a title)
_META_TAGS = 5  # genres / moods kept per book
_WARNING_TAGS = 8  # content warnings kept per book (worth showing more of)
_DESCRIPTION_CAP = 1500

# Hardcover's `book_category_id` / `literary_type_id` enums (from their docs) —
# mapped to strings here so the viewer never has to know the numbers.
_BOOK_CATEGORY = {
    1: "Book",
    2: "Novella",
    3: "Short Story",
    4: "Graphic Novel",
    5: "Fan Fiction",
    6: "Research Paper",
    7: "Poetry",
    8: "Collection",
    9: "Web Novel",
    10: "Light Novel",
}
_LITERARY_TYPE = {1: "Fiction", 2: "Nonfiction"}

_BOOK_BY_ISBN = """
query BookBrainSimilarByIsbn($isbn: String!) {
  editions(
    where: {_or: [{isbn_13: {_eq: $isbn}}, {isbn_10: {_eq: $isbn}}]}
    limit: 1
    order_by: {users_count: desc}
  ) {
    book {
      id
      cached_similar_book_ids
      rating
      ratings_count
      pages
      book_category_id
      literary_type_id
      cached_tags
      lists_count
      description
    }
  }
}
"""

_RESOLVE_BOOKS = """
query BookBrainResolveBooks($ids: [Int!]!) {
  books(where: {id: {_in: $ids}}) {
    id
    title
    contributions(limit: 1) { author { name } }
    editions(
      where: {isbn_13: {_is_null: false}}
      limit: 1
      order_by: {users_count: desc}
    ) { isbn_13 }
  }
}
"""


def _tag_names(cached_tags: object, key: str, limit: int = _META_TAGS) -> list[str]:
    """Pull the top few tag names out of one `cached_tags` bucket
    (`Genre` / `Mood` / `Content Warning` / …). The bucket is a list of
    {tag, count, …}, already ordered most-used first."""
    bucket = cached_tags.get(key) if isinstance(cached_tags, dict) else None
    out: list[str] = []
    for entry in bucket or []:
        name = entry.get("tag") if isinstance(entry, dict) else None
        if isinstance(name, str) and name.strip():
            out.append(name.strip())
        if len(out) >= limit:
            break
    return out


def _book_meta(book: dict) -> dict:
    """The curated per-book fields the viewer shows as badges / facets. Only
    keys that are actually set land in the dict."""
    meta: dict = {}
    rating = book.get("rating")
    if isinstance(rating, int | float):
        meta["rating"] = round(float(rating), 2)
    ratings_count = book.get("ratings_count")
    if isinstance(ratings_count, int) and ratings_count > 0:
        meta["ratingsCount"] = ratings_count
    pages = book.get("pages")
    if isinstance(pages, int) and pages > 0:
        meta["pages"] = pages
    category = _BOOK_CATEGORY.get(book.get("book_category_id"))
    if category:
        meta["category"] = category
    literary_type = _LITERARY_TYPE.get(book.get("literary_type_id"))
    if literary_type:
        meta["literaryType"] = literary_type
    genres = _tag_names(book.get("cached_tags"), "Genre")
    if genres:
        meta["genres"] = genres
    moods = _tag_names(book.get("cached_tags"), "Mood")
    if moods:
        meta["moods"] = moods
    warnings = _tag_names(book.get("cached_tags"), "Content Warning", _WARNING_TAGS)
    if warnings:
        meta["contentWarnings"] = warnings
    lists_count = book.get("lists_count")
    if isinstance(lists_count, int) and lists_count > 0:
        meta["listsCount"] = lists_count
    description = book.get("description")
    if isinstance(description, str) and description.strip():
        meta["description"] = " ".join(description.split())[:_DESCRIPTION_CAP]
    return meta


async def _similar_ids(
    client: httpx.AsyncClient, token: str, bucket: _TokenBucket, isbn: str
) -> tuple[int, list[int], dict] | None:
    data = await hardcover_graphql(client, token, _BOOK_BY_ISBN, {"isbn": isbn}, bucket)
    if data is None:
        raise HardcoverUnavailable  # call failed — don't wipe existing recs/meta
    editions = data.get("editions") or []
    if not editions:
        return None  # Hardcover genuinely has no edition for this ISBN
    book = editions[0].get("book") or {}
    hc_id = book.get("id")
    if not isinstance(hc_id, int):
        return None
    ids = [i for i in (book.get("cached_similar_book_ids") or []) if isinstance(i, int)]
    return hc_id, ids, _book_meta(book)


async def _resolve(
    client: httpx.AsyncClient, token: str, bucket: _TokenBucket, ids: list[int], self_id: int
) -> list[dict]:
    if not ids:
        return []
    data = await hardcover_graphql(client, token, _RESOLVE_BOOKS, {"ids": ids[:_RESOLVE]}, bucket)
    if data is None:
        raise HardcoverUnavailable  # call failed — don't store a truncated list
    rows = {b["id"]: b for b in (data.get("books") or []) if isinstance(b.get("id"), int)}
    out: list[dict] = []
    for bid in ids:  # keep Hardcover's similarity ranking
        if bid == self_id or bid not in rows:
            continue
        b = rows[bid]
        title = b.get("title")
        if not isinstance(title, str) or not title.strip():
            continue
        contribs = b.get("contributions") or []
        author = None
        if contribs and isinstance(contribs[0], dict):
            a = contribs[0].get("author")
            if isinstance(a, dict):
                author = a.get("name")
        editions = b.get("editions") or []
        isbn13 = editions[0].get("isbn_13") if editions and isinstance(editions[0], dict) else None
        out.append({"title": title.strip(), "author": author, "isbn13": isbn13})
        if len(out) >= _KEEP:
            break
    return out


async def refresh_book_recs(
    session: AsyncSession,
    *,
    limit: int = 400,
    stale_after_days: int = 45,
    client: httpx.AsyncClient | None = None,
) -> dict:
    settings = get_settings()
    token = (settings.hardcover_api_token or "").strip()
    if not token:
        return {"skipped": "no HARDCOVER_API_TOKEN"}

    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=stale_after_days)
    books = (
        (
            await session.execute(
                select(Book)
                .join(File, File.book_id == Book.id)
                .join(Identifier, Identifier.book_id == Book.id)
                .where(File.status == FileStatus.organised)
                .where(
                    Identifier.type.in_([IdentifierType.isbn13, IdentifierType.isbn10])
                )
                .where(
                    or_(
                        Book.hardcover_synced_at.is_(None),
                        Book.hardcover_synced_at < cutoff,
                        # prompts/26 Part B — rows synced by Phase 3 before
                        # `meta` existed get one backfill pass now rather than
                        # waiting out the 45-day staleness window.
                        and_(
                            Book.hardcover_json.is_not(None),
                            func.json_extract(Book.hardcover_json, "$.meta").is_(None),
                        ),
                    )
                )
                .distinct()
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )

    isbns = dict(
        (
            await session.execute(
                select(Identifier.book_id, Identifier.value)
                .where(Identifier.book_id.in_([b.id for b in books]))
                .where(Identifier.type.in_([IdentifierType.isbn13, IdentifierType.isbn10]))
                .order_by(Identifier.type)  # isbn13 sorts before isbn10, wins the dict
            )
        ).all()
    )

    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=12.0)
    bucket = _TokenBucket(rate_per_sec=0.9, burst=8)
    counts = {"resolved": 0, "empty": 0, "failed": 0}
    stopped_early = False

    try:
        for book in books:
            isbn = isbns.get(book.id)
            try:
                found = await _similar_ids(http, token, bucket, isbn) if isbn else None
                if found is None:
                    # `meta: {}` records "we looked, Hardcover doesn't have
                    # this ISBN" so the backfill query above doesn't re-pick it
                    # every night.
                    book.hardcover_json = {"similar": [], "meta": {}}
                    book.hardcover_synced_at = datetime.now(UTC).replace(tzinfo=None)
                    counts["empty"] += 1
                    continue
                hc_id, ids, meta = found
                similar = await _resolve(http, token, bucket, ids, hc_id)
                book.hardcover_json = {"id": hc_id, "similar": similar, "meta": meta}
                book.hardcover_synced_at = datetime.now(UTC).replace(tzinfo=None)
                counts["resolved" if similar else "empty"] += 1
            except HardcoverRateLimited:
                # Daily quota gone — stop, keep what's committed, resume next
                # run. Do NOT wipe this book's existing recs/meta.
                stopped_early = True
                break
            except HardcoverUnavailable:
                # Transient call failure — leave this book's existing data
                # alone rather than storing an empty/truncated list.
                counts["failed"] += 1
                await session.rollback()
            except Exception:  # noqa: BLE001 — one bad book must not stop the run
                logger.exception("hardcover recs failed for book %s", book.id)
                counts["failed"] += 1
                await session.rollback()
            finally:
                # Commit per book, not once at the end — each iteration does
                # ~2 slow HTTP calls, so a single transaction would hold the
                # SQLite write lock for minutes and time out anything else
                # trying to write meanwhile.
                try:
                    await session.commit()
                except Exception:  # noqa: BLE001
                    logger.exception("hardcover recs: per-row commit failed")
                    await session.rollback()
    finally:
        if owns_client:
            await http.aclose()

    if stopped_early:
        counts["rate_limited"] = True
    logger.info("hardcover book recs: %s", counts)
    return counts


def dedupe_against_self(recs: list[dict], own_title: str | None) -> list[dict]:
    """Belt-and-braces: drop a rec that normalises to the same title as the
    book it's attached to (Hardcover occasionally lists an alternate edition
    as 'similar')."""
    own = normalize_title(own_title) if own_title else ""
    return [r for r in recs if not own or normalize_title(r.get("title")) != own]
