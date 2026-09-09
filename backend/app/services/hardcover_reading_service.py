"""prompts/30 Phase 1 — pull the account's Hardcover reading data (Read /
Reading / Want to read / DNF + ratings + read dates) so the library-viewer
can show a "Read" badge/filter and, later, "what to read next".

**Licence:** this is the one place BookBrain reads Hardcover *user* data, not
just catalogue data. It's defensible only because it reads the *owner's own*
`user_books` with the *owner's own* Personal Access Token, for the *owner's
own* self-hosted tool, and never redistributes it — the sidecar lives in the
owner's private Drive folder and the viewer attributes the status to a named
reader. If the viewer is ever opened to a wider audience than the household,
revisit whether this still holds. **Read-only** — no write-back to Hardcover.

Stateless: fetch live, match against the library in build_reading_payload,
write the sidecar. Nothing persisted server-side. One paged query per night.
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
