"""prompts/30 Phase 1 — pull the account's Hardcover reading data (Read /
Reading / Want to read / DNF + ratings + read dates) so the library-viewer
can show a "Read" badge/filter and, later, "what to read next".

**Licence:** this is the one place BookBrain reads Hardcover *user* data, not
just catalogue data. It's defensible only because it reads the *owner's own*
`user_books` with the *owner's own* Personal Access Token, for the *owner's
own* self-hosted tool, and never redistributes it — the sidecar lives in the
owner's private Drive folder and the viewer attributes the status to a named
reader. If the viewer is ever opened to a wider audience than the household,
revisit whether this still holds.

prompts/30 Phase 3 adds **write-back**: the viewer queues "mark read" (etc.)
in a Drive file and `apply_pending` applies it to Hardcover on the next sync.
Same licence rationale — the owner marking their own books read via their own
token is the personal-automation case a PAT exists for. Only *status*
(read/reading/want/dnf) and, since prompts/31 Part I, *reader position*
(`progress_pages`, advance-only) are written — never reviews or anyone else's
data. Part I also pulls Hardcover's own position back into the sidecar so the
viewer can show "N% on Hardcover" for a book you started on another device.

Fetch is stateless: pull live, match in build_reading_payload, write the
sidecar. One paged query per night.
"""

import logging

import httpx

from app.core.config import get_settings
from app.providers.metadata.hardcover import (
    HardcoverRateLimited,
    _TokenBucket,
    hardcover_graphql,
)

logger = logging.getLogger(__name__)

# `statuses` enum table isn't readable by an API token (verified live), so the
# ids are mapped by hand. Seen in use: 1/2/3/5. Anything else → None.
_STATUS = {1: "want", 2: "reading", 3: "read", 5: "dnf"}
_STATUS_ID = {v: k for k, v in _STATUS.items()}
_PAGE = 100
# A page returning None is a transient failure, not end-of-data — retry before
# giving up (a truncated pull makes read books look unread). Tests set to 0.
_PAGE_RETRIES = 3
_PAGE_RETRY_BACKOFF = 1.5

_READING = """
query BookBrainReading($limit: Int!, $offset: Int!) {
  me {
    username
    user_books(limit: $limit, offset: $offset, order_by: {id: asc}) {
      status_id
      rating
      last_read_date
      first_read_date
      read_count
      user_book_reads(order_by: {id: desc}, limit: 1) { progress_pages }
      book {
        title
        pages
        contributions(where: {_or: [{contribution: {_eq: "Author"}}, {contribution: {_is_null: true}}]}, limit: 1) { author { name } }
        editions(where: {isbn_13: {_is_null: false}}, limit: 1,
                 order_by: {users_count: desc}) { isbn_13 }
      }
    }
  }
}
"""

# prompts/31 Part C — the owner's reading goal. `progress` is Hardcover's own
# count (all books, not just ones in the library), which is exactly what the
# viewer should show. Verified live: {goal: 50, progress: 48.0, metric: "book",
# start_date: "2025-12-31", end_date: "2026-12-30", description: "2026 Reading Goal"}.
_GOAL = """
query BookBrainReadingGoal {
  me {
    goals {
      goal
      progress
      metric
      start_date
      end_date
    }
  }
}
"""


def _map_row(row: dict) -> dict | None:
    book = row.get("book") or {}
    title = book.get("title")
    if not isinstance(title, str) or not title.strip():
        return None
    status = _STATUS.get(row.get("status_id"))
    rating = row.get("rating")
    rating = float(rating) if isinstance(rating, int | float) else None
    if status is None and rating is None:
        return None
    contribs = book.get("contributions") or []
    author = None
    if contribs and isinstance(contribs[0], dict):
        a = contribs[0].get("author")
        if isinstance(a, dict) and isinstance(a.get("name"), str):
            author = a["name"].strip()
    editions = book.get("editions") or []
    isbn13 = editions[0].get("isbn_13") if editions and isinstance(editions[0], dict) else None
    # prompts/31 Part I — reader position, as a fraction, when Hardcover has one.
    pages = book.get("pages")
    reads = row.get("user_book_reads") or []
    pp = reads[0].get("progress_pages") if reads and isinstance(reads[0], dict) else None
    progress = None
    if isinstance(pp, int) and pp > 0 and isinstance(pages, int) and pages > 0:
        progress = round(min(1.0, pp / pages), 3)
    return {
        "title": title.strip(),
        "author": author,
        "isbn13": isbn13 if isinstance(isbn13, str) and isbn13.strip() else None,
        "status": status,
        "rating": round(rating, 2) if rating is not None else None,
        "readDate": row.get("last_read_date") or row.get("first_read_date"),
        "readCount": row.get("read_count") if isinstance(row.get("read_count"), int) else 0,
        "progress": progress,
    }


# prompts/30 Phase 3 — write-back. The viewer can't call Hardcover (static
# site, token is a backend secret), so it queues changes in a Drive file and
# these mutations apply them on the next sync. **Writes to Hardcover** — see
# the module docstring's licence note; still the owner's own account/token.
_BOOK_BY_ISBN = """
query BookBrainReadingBookByIsbn($isbn: String!) {
  editions(where: {_or: [{isbn_13: {_eq: $isbn}}, {isbn_10: {_eq: $isbn}}]},
           limit: 1, order_by: {users_count: desc}) { book { id } }
}
"""
_BOOK_SEARCH = """
query BookBrainReadingBookSearch($q: String!) {
  search(query: $q, query_type: "Book", per_page: 3) { results }
}
"""
_EXISTING_UB = """
query BookBrainExistingUserBook($id: Int!) {
  me {
    user_books(where: {book_id: {_eq: $id}}, limit: 1) {
      id
      status_id
      book { pages }
      user_book_reads(order_by: {id: desc}, limit: 1) { id progress_pages }
    }
  }
}
"""
_UPDATE_UB = """
mutation BookBrainUpdateUserBook($id: Int!, $obj: UserBookUpdateInput!) {
  update_user_book(id: $id, object: $obj) { id }
}
"""
_INSERT_UB = """
mutation BookBrainInsertUserBook($obj: UserBookCreateInput!) {
  insert_user_book(object: $obj) { id }
}
"""
# prompts/31 Part I — reading-progress write-back.
_UPDATE_UBR = """
mutation BookBrainUpdateRead($id: Int!, $obj: DatesReadInput!) {
  update_user_book_read(id: $id, object: $obj) { id }
}
"""
_INSERT_UBR = """
mutation BookBrainInsertRead($ubId: Int!, $obj: DatesReadInput!) {
  insert_user_book_read(user_book_id: $ubId, user_book_read: $obj) { id }
}
"""


async def _resolve_book_id(
    http: httpx.AsyncClient, token: str, bucket: _TokenBucket, change: dict
) -> int | None:
    isbn = (change.get("isbn13") or "").strip()
    if isbn:
        data = await hardcover_graphql(http, token, _BOOK_BY_ISBN, {"isbn": isbn}, bucket)
        eds = (data or {}).get("editions") or []
        if eds and isinstance(eds[0].get("book"), dict):
            bid = eds[0]["book"].get("id")
            if isinstance(bid, int):
                return bid
    title = (change.get("title") or "").strip()
    if not title:
        return None
    author = (change.get("author") or "").strip()
    q = f"{title} {author}".strip()
    data = await hardcover_graphql(http, token, _BOOK_SEARCH, {"q": q}, bucket)
    hits = (((data or {}).get("search") or {}).get("results") or {}).get("hits") or []
    for hit in hits[:1]:
        doc = hit.get("document") if isinstance(hit, dict) else None
        if isinstance(doc, dict):
            try:
                return int(doc["id"])
            except (KeyError, TypeError, ValueError):
                pass
    return None


async def _apply_status(
    http: httpx.AsyncClient, token: str, bucket: _TokenBucket, ub: dict | None, book_id: int, status_id: int
) -> bool:
    from datetime import date

    obj: dict = {"status_id": status_id}
    if status_id == 3:
        obj["last_read_date"] = date.today().isoformat()
    if ub is not None:
        if ub.get("status_id") == status_id:
            return True  # already there
        res = await hardcover_graphql(http, token, _UPDATE_UB, {"id": ub["id"], "obj": obj}, bucket)
    else:
        res = await hardcover_graphql(
            http, token, _INSERT_UB, {"obj": {"book_id": book_id, **obj}}, bucket
        )
    return res is not None


async def _apply_progress(
    http: httpx.AsyncClient,
    token: str,
    bucket: _TokenBucket,
    ub: dict | None,
    book_id: int,
    percent: float,
) -> bool:
    """Advance-only: push the reader's position to Hardcover as
    `progress_pages`, never reduce it, skip if within ~2%. `ub` is the row
    from `_EXISTING_UB` (has `book.pages` + the latest `user_book_reads`)."""
    from datetime import date

    percent = max(0.0, min(1.0, percent))
    pages = ((ub or {}).get("book") or {}).get("pages") if ub else None
    if not isinstance(pages, int) or pages <= 0:
        return True  # can't convert a % to pages — nothing to do, not a failure
    target = round(percent * pages)
    if target <= 0:
        return True

    if ub is None:
        # No user_book at all — start it as "currently reading" with this progress.
        res = await hardcover_graphql(
            http, token, _INSERT_UB, {"obj": {"book_id": book_id, "status_id": 2}}, bucket
        )
        if res is None:
            return False
        data = await hardcover_graphql(http, token, _EXISTING_UB, {"id": book_id}, bucket)
        ub = (((data or {}).get("me") or [{}])[0].get("user_books") or [None])[0]
        if ub is None:
            return False

    reads = ub.get("user_book_reads") or []
    existing = reads[0] if reads and isinstance(reads[0], dict) else None
    current = existing.get("progress_pages") if existing else None
    if isinstance(current, int):
        if target <= current or (target - current) / pages < 0.02:
            return True  # already there / trivial advance
        return (
            await hardcover_graphql(
                http, token, _UPDATE_UBR, {"id": existing["id"], "obj": {"progress_pages": target}}, bucket
            )
            is not None
        )
    obj = {"progress_pages": target, "started_at": date.today().isoformat()}
    if existing:
        return (
            await hardcover_graphql(
                http, token, _UPDATE_UBR, {"id": existing["id"], "obj": obj}, bucket
            )
            is not None
        )
    return (
        await hardcover_graphql(http, token, _INSERT_UBR, {"ubId": ub["id"], "obj": obj}, bucket)
        is not None
    )


async def apply_pending(changes: list[dict], *, client: httpx.AsyncClient | None = None) -> dict:
    """Apply queued reading changes to Hardcover — a status (`status`), a
    reader position (`progressPercent`, 0..1, advance-only), or both. Returns
    `{applied: [driveFileId...], failed: [driveFileId...]}`; the caller drops
    the applied ones. Idempotent, best-effort per change."""
    settings = get_settings()
    token = (settings.hardcover_api_token or "").strip()
    if not token or not changes:
        return {"applied": [], "failed": []}

    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=15.0)
    bucket = _TokenBucket(rate_per_sec=0.9, burst=8)
    applied: list[str] = []
    failed: list[str] = []

    try:
        for change in changes:
            drive_id = change.get("driveFileId")
            status_id = _STATUS_ID.get(change.get("status"))
            progress = change.get("progressPercent")
            has_progress = isinstance(progress, int | float) and 0.0 < progress < 1.0
            if not isinstance(drive_id, str) or (status_id is None and not has_progress):
                continue
            try:
                book_id = await _resolve_book_id(http, token, bucket, change)
                if book_id is None:
                    failed.append(drive_id)
                    continue
                data = await hardcover_graphql(http, token, _EXISTING_UB, {"id": book_id}, bucket)
                ub = (((data or {}).get("me") or [{}])[0].get("user_books") or [None])[0]

                ok = True
                if status_id is not None:
                    ok = await _apply_status(http, token, bucket, ub, book_id, status_id)
                # Don't push a partial position onto a book just marked read.
                if ok and has_progress and status_id != 3:
                    ok = await _apply_progress(http, token, bucket, ub, book_id, float(progress))
                (applied if ok else failed).append(drive_id)
            except HardcoverRateLimited:
                failed.append(drive_id)
                break
            except Exception:  # noqa: BLE001 — one bad change must not stop the rest
                logger.exception("reading write-back failed for %s", drive_id)
                failed.append(drive_id)
    finally:
        if owns_client:
            await http.aclose()

    logger.info("reading write-back: %d applied, %d failed", len(applied), len(failed))
    return {"applied": applied, "failed": failed}


async def fetch_reading(
    *, client: httpx.AsyncClient | None = None
) -> tuple[list[dict], str | None, bool]:
    """Returns (rows, reader_username, complete). `complete` is False if a page
    failed (transient error, rate limit) — the caller must NOT overwrite a good
    sidecar with a partial one, or read books look unread (a partial pull is
    worse than a stale one). A page returning `None` is a *transient failure*,
    not end-of-data: it's retried before we give up."""
    import asyncio

    settings = get_settings()
    token = (settings.hardcover_api_token or "").strip()
    if not token:
        return [], None, False

    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=15.0)
    bucket = _TokenBucket(rate_per_sec=0.9, burst=8)
    out: list[dict] = []
    username: str | None = None
    complete = False
    try:
        for offset in range(0, 20_000, _PAGE):
            data = None
            for attempt in range(_PAGE_RETRIES + 1):  # a blip must not truncate the pull
                data = await hardcover_graphql(
                    http, token, _READING, {"limit": _PAGE, "offset": offset}, bucket
                )
                if data is not None or attempt == _PAGE_RETRIES:
                    break
                await asyncio.sleep(_PAGE_RETRY_BACKOFF * (attempt + 1))
            if data is None:
                logger.warning("hardcover reading: page at offset %d kept failing — partial", offset)
                break  # complete stays False
            me = data.get("me") or []
            if not me or not isinstance(me[0], dict):
                complete = True  # genuinely no more data
                break
            username = username or me[0].get("username")
            rows = me[0].get("user_books") or []
            for row in rows:
                mapped = _map_row(row) if isinstance(row, dict) else None
                if mapped is not None:
                    out.append(mapped)
            if len(rows) < _PAGE:
                complete = True
                break
    except HardcoverRateLimited:
        logger.warning("hardcover reading: daily limit hit mid-fetch, using %d rows (partial)", len(out))
    except Exception:  # noqa: BLE001 — best-effort, never fail the caller
        logger.exception("hardcover reading fetch failed")
    finally:
        if owns_client:
            await http.aclose()

    logger.info(
        "hardcover reading: %d rows (reader %s, %s)",
        len(out),
        username,
        "complete" if complete else "PARTIAL",
    )
    return out, username, complete


async def fetch_goal(*, client: httpx.AsyncClient | None = None) -> dict | None:
    """The owner's current book-count reading goal (prompts/31 Part C), or None.
    Picks the `book`-metric goal whose date range covers today; falls back to
    the first book goal. Best-effort — any failure returns None."""
    from datetime import date

    settings = get_settings()
    token = (settings.hardcover_api_token or "").strip()
    if not token:
        return None

    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=15.0)
    bucket = _TokenBucket(rate_per_sec=0.9, burst=8)
    try:
        data = await hardcover_graphql(http, token, _GOAL, {}, bucket)
    except Exception:  # noqa: BLE001 — best-effort
        logger.exception("hardcover reading goal fetch failed")
        return None
    finally:
        if owns_client:
            await http.aclose()

    me = (data or {}).get("me") or []
    goals = me[0].get("goals") if me and isinstance(me[0], dict) else None
    book_goals = [
        g
        for g in (goals or [])
        if isinstance(g, dict) and g.get("metric") == "book" and isinstance(g.get("goal"), int)
    ]
    if not book_goals:
        return None
    today = date.today().isoformat()
    current = next(
        (g for g in book_goals if (g.get("start_date") or "") <= today <= (g.get("end_date") or "9999")),
        book_goals[0],
    )
    end = current.get("end_date") or ""
    year = int(end[:4]) if end[:4].isdigit() else date.today().year
    progress = current.get("progress")
    return {
        "year": year,
        "target": current["goal"],
        "progress": int(progress) if isinstance(progress, int | float) else 0,
    }
