"""prompts/31 Part E2 — Hardcover's curated (featured) lists that feature books
James owns → the *other* books on those lists become wishlist candidates
("if you own half of 'NPR Top 100 SFF', here's the rest").

Catalogue data — featured lists are public, editorially curated. Best-effort:
two Hardcover calls, any failure returns an empty result.
"""

from __future__ import annotations

import logging

import httpx

from app.core.config import get_settings
from app.providers.metadata.hardcover import (
    HardcoverRateLimited,
    _TokenBucket,
    hardcover_graphql,
)

logger = logging.getLogger(__name__)

_LIST_COUNT = 12  # featured lists to pull
_BOOKS_PER_LIST = 60
_MAX_CANDIDATES = 80
# The `_in` filter takes the owned Hardcover ids; cap so the query stays sane.
_MAX_OWNED_IDS = 600

_LISTS = """
query BookBrainLists($ids: [Int!]!, $limit: Int!) {
  lists(
    where: {list_books: {book_id: {_in: $ids}}, featured: {_eq: true}}
    order_by: {followers_count: desc_nulls_last}
    limit: $limit
  ) {
    id
    name
    slug
    books_count
  }
}
"""
_LIST_BOOKS = """
query BookBrainListBooks($ids: [Int!]!, $per: Int!) {
  lists(where: {id: {_in: $ids}}) {
    id
    name
    slug
    list_books(limit: $per) {
      book {
        id
        title
        contributions(where: {_or: [{contribution: {_eq: "Author"}}, {contribution: {_is_null: true}}]}, limit: 1) { author { name } }
        editions(where: {isbn_13: {_is_null: false}}, limit: 1, order_by: {users_count: desc}) {
          isbn_13
        }
      }
    }
  }
}
"""


async def fetch_list_candidates(
    owned_hc_ids: set[int], *, client: httpx.AsyncClient | None = None
) -> dict:
    """Returns `{"lists": [{name, slug, owned, total}], "candidates":
    [{title, author, isbn13, fromList}]}`. Best-effort → empty."""
    settings = get_settings()
    token = (settings.hardcover_api_token or "").strip()
    if not token or not owned_hc_ids:
        return {"lists": [], "candidates": []}

    ids = list(owned_hc_ids)[:_MAX_OWNED_IDS]
    owned = set(owned_hc_ids)
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=20.0)
    bucket = _TokenBucket(rate_per_sec=0.9, burst=8)
    try:
        data = await hardcover_graphql(http, token, _LISTS, {"ids": ids, "limit": _LIST_COUNT}, bucket)
        lists = [
            row
            for row in ((data or {}).get("lists") or [])
            if isinstance(row, dict) and isinstance(row.get("id"), int)
        ]
        if not lists:
            return {"lists": [], "candidates": []}
        data = await hardcover_graphql(
            http,
            token,
            _LIST_BOOKS,
            {"ids": [row["id"] for row in lists], "per": _BOOKS_PER_LIST},
            bucket,
        )
    except HardcoverRateLimited:
        return {"lists": [], "candidates": []}
    except Exception:  # noqa: BLE001 — best-effort
        logger.exception("hardcover lists fetch failed")
        return {"lists": [], "candidates": []}
    finally:
        if owns_client:
            await http.aclose()

    out_lists: list[dict] = []
    candidates: list[dict] = []
    seen: set[str] = set()
    for row in (data or {}).get("lists") or []:
        if not isinstance(row, dict):
            continue
        name = row.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        books = [
            lb.get("book")
            for lb in row.get("list_books") or []
            if isinstance(lb, dict) and isinstance(lb.get("book"), dict)
        ]
        owned_here = sum(1 for b in books if b.get("id") in owned)
        out_lists.append(
            {
                "name": name.strip(),
                "slug": row.get("slug") if isinstance(row.get("slug"), str) else None,
                "owned": owned_here,
                "total": len(books),
            }
        )
        for b in books:
            if b.get("id") in owned:
                continue
            title = b.get("title")
            if not isinstance(title, str) or not title.strip():
                continue
            contribs = b.get("contributions") or []
            author = contribs[0].get("author") if contribs and isinstance(contribs[0], dict) else None
            author_name = author.get("name") if isinstance(author, dict) else None
            eds = b.get("editions") or []
            isbn13 = eds[0].get("isbn_13") if eds and isinstance(eds[0], dict) else None
            key = f"{title.strip().lower()}|{(author_name or '').strip().lower()}"
            if key in seen:
                continue
            seen.add(key)
            candidates.append(
                {
                    "title": title.strip(),
                    "author": author_name.strip() if isinstance(author_name, str) else None,
                    "isbn13": isbn13.strip() if isinstance(isbn13, str) and isbn13.strip() else None,
                    "fromList": name.strip(),
                }
            )

    # front-load candidates from the lists we own the most of
    rank = {row["name"]: row["owned"] for row in out_lists}
    candidates.sort(key=lambda c: rank.get(c["fromList"], 0), reverse=True)
    logger.info(
        "hardcover lists: %d lists, %d candidates", len(out_lists), len(candidates[:_MAX_CANDIDATES])
    )
    return {"lists": out_lists, "candidates": candidates[:_MAX_CANDIDATES]}
