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
"""

import logging
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.data.models import Book, File, FileStatus, Identifier, IdentifierType
from app.providers.metadata.hardcover import _TokenBucket, hardcover_graphql
from app.services.text_match import normalize_title

logger = logging.getLogger(__name__)

_KEEP = 15  # recs stored per book
_RESOLVE = 25  # top similar ids we bother resolving (some won't have a title)

_BOOK_BY_ISBN = """
query BookBrainSimilarByIsbn($isbn: String!) {
  editions(
    where: {_or: [{isbn_13: {_eq: $isbn}}, {isbn_10: {_eq: $isbn}}]}
    limit: 1
    order_by: {users_count: desc}
  ) {
    book { id cached_similar_book_ids }
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


async def _similar_ids(
    client: httpx.AsyncClient, token: str, bucket: _TokenBucket, isbn: str
) -> tuple[int, list[int]] | None:
    data = await hardcover_graphql(client, token, _BOOK_BY_ISBN, {"isbn": isbn}, bucket)
    editions = (data or {}).get("editions") or []
    if not editions:
        return None
    book = editions[0].get("book") or {}
    hc_id = book.get("id")
    if not isinstance(hc_id, int):
        return None
    ids = [i for i in (book.get("cached_similar_book_ids") or []) if isinstance(i, int)]
    return hc_id, ids


async def _resolve(
    client: httpx.AsyncClient, token: str, bucket: _TokenBucket, ids: list[int], self_id: int
) -> list[dict]:
    if not ids:
        return []
    data = await hardcover_graphql(client, token, _RESOLVE_BOOKS, {"ids": ids[:_RESOLVE]}, bucket)
    rows = {b["id"]: b for b in ((data or {}).get("books") or []) if isinstance(b.get("id"), int)}
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

    try:
        for book in books:
            isbn = isbns.get(book.id)
            try:
                found = await _similar_ids(http, token, bucket, isbn) if isbn else None
                if found is None:
                    book.hardcover_json = {"similar": []}
                    book.hardcover_synced_at = datetime.now(UTC).replace(tzinfo=None)
                    counts["empty"] += 1
                    continue
                hc_id, ids = found
                similar = await _resolve(http, token, bucket, ids, hc_id)
                book.hardcover_json = {"id": hc_id, "similar": similar}
                book.hardcover_synced_at = datetime.now(UTC).replace(tzinfo=None)
                counts["resolved" if similar else "empty"] += 1
            except Exception:  # noqa: BLE001 — one bad book must not stop the run
                logger.exception("hardcover recs failed for book %s", book.id)
                counts["failed"] += 1

        await session.commit()
    finally:
        if owns_client:
            await http.aclose()

    logger.info("hardcover book recs: %s", counts)
    return counts


def dedupe_against_self(recs: list[dict], own_title: str | None) -> list[dict]:
    """Belt-and-braces: drop a rec that normalises to the same title as the
    book it's attached to (Hardcover occasionally lists an alternate edition
    as 'similar')."""
    own = normalize_title(own_title) if own_title else ""
    return [r for r in recs if not own or normalize_title(r.get("title")) != own]
