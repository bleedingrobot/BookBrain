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
token is the personal-automation case a PAT exists for. Only *status* is
written (read/reading/want/dnf), never reviews or anyone else's data.

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
      book {
        title
        contributions(limit: 1) { author { name } }
        editions(where: {isbn_13: {_is_null: false}}, limit: 1,
                 order_by: {users_count: desc}) { isbn_13 }
      }
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
    return {
        "title": title.strip(),
        "author": author,
        "isbn13": isbn13 if isinstance(isbn13, str) and isbn13.strip() else None,
        "status": status,
        "rating": round(rating, 2) if rating is not None else None,
        "readDate": row.get("last_read_date") or row.get("first_read_date"),
        "readCount": row.get("read_count") if isinstance(row.get("read_count"), int) else 0,
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
  me { user_books(where: {book_id: {_eq: $id}}, limit: 1) { id status_id } }
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


async def apply_pending(changes: list[dict], *, client: httpx.AsyncClient | None = None) -> dict:
    """Apply queued reading-status changes to Hardcover. Returns
    `{applied: [driveFileId...], failed: [driveFileId...]}` — the caller drops
    the applied ones from the Drive queue. Idempotent: a change that already
    matches Hardcover counts as applied. Best-effort per change."""
    settings = get_settings()
    token = (settings.hardcover_api_token or "").strip()
    if not token or not changes:
        return {"applied": [], "failed": []}

    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=15.0)
    bucket = _TokenBucket(rate_per_sec=0.9, burst=8)
    applied: list[str] = []
    failed: list[str] = []
    from datetime import date

    try:
        for change in changes:
            drive_id = change.get("driveFileId")
            status_id = _STATUS_ID.get(change.get("status"))
            if not isinstance(drive_id, str) or status_id is None:
                continue
            try:
                book_id = await _resolve_book_id(http, token, bucket, change)
                if book_id is None:
                    failed.append(drive_id)
                    continue
                data = await hardcover_graphql(http, token, _EXISTING_UB, {"id": book_id}, bucket)
                ubs = ((data or {}).get("me") or [{}])[0].get("user_books") or []
                obj: dict = {"status_id": status_id}
                if status_id == 3:
                    obj["last_read_date"] = date.today().isoformat()
                if ubs:
                    if ubs[0].get("status_id") == status_id:
                        applied.append(drive_id)  # already there
                        continue
                    res = await hardcover_graphql(
                        http, token, _UPDATE_UB, {"id": ubs[0]["id"], "obj": obj}, bucket
                    )
                else:
                    res = await hardcover_graphql(
                        http, token, _INSERT_UB, {"obj": {"book_id": book_id, **obj}}, bucket
                    )
                (applied if res is not None else failed).append(drive_id)
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


async def fetch_reading(*, client: httpx.AsyncClient | None = None) -> tuple[list[dict], str | None]:
    """Returns (rows, reader_username). Best-effort: any failure (no token,
    network, rate limit) returns whatever pages came back before it, or
    ([], None)."""
    settings = get_settings()
    token = (settings.hardcover_api_token or "").strip()
    if not token:
        return [], None

    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=15.0)
    bucket = _TokenBucket(rate_per_sec=0.9, burst=8)
    out: list[dict] = []
    username: str | None = None
    try:
        for offset in range(0, 20_000, _PAGE):
            data = await hardcover_graphql(
                http, token, _READING, {"limit": _PAGE, "offset": offset}, bucket
            )
            me = (data or {}).get("me") or []
            if not me or not isinstance(me[0], dict):
                break
            username = username or me[0].get("username")
            rows = me[0].get("user_books") or []
            for row in rows:
                mapped = _map_row(row) if isinstance(row, dict) else None
                if mapped is not None:
                    out.append(mapped)
            if len(rows) < _PAGE:
                break
    except HardcoverRateLimited:
        logger.warning("hardcover reading: daily limit hit mid-fetch, using %d rows", len(out))
    except Exception:  # noqa: BLE001 — best-effort, never fail the caller
        logger.exception("hardcover reading fetch failed")
    finally:
        if owns_client:
            await http.aclose()

    logger.info("hardcover reading: %d rows (reader %s)", len(out), username)
    return out, username
