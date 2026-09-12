"""prompts/31 Part F — Hardcover's "Prompts" (a community question with book
answers: "What's your favorite scifi with a strong sense of place?") mapped to
the books James owns, so the viewer can show a browsable "your library
answers" screen.

Catalogue data — the questions and their book lists are public. Best-effort:
two Hardcover calls, any failure returns [].
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

_PROMPT_COUNT = 60  # top prompts by answer count to consider
_BOOKS_PER_PROMPT = 40  # answers pulled per prompt

_TOP_PROMPTS = """
query BookBrainPrompts($limit: Int!) {
  prompts(order_by: {answers_count: desc_nulls_last}, limit: $limit) {
    id
    question
    slug
  }
}
"""
_PROMPT_BOOKS = """
query BookBrainPromptBooks($ids: [Int!]!, $per: Int!) {
  prompts(where: {id: {_in: $ids}}) {
    id
    prompt_books(limit: $per) { book_id }
  }
}
"""


async def fetch_prompts(*, client: httpx.AsyncClient | None = None) -> list[dict]:
    """Returns `[{id, question, slug, bookIds: [hcId...]}]` for the top
    prompts. Best-effort → []."""
    settings = get_settings()
    token = (settings.hardcover_api_token or "").strip()
    if not token:
        return []
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=15.0)
    bucket = _TokenBucket(rate_per_sec=0.9, burst=8)
    try:
        data = await hardcover_graphql(http, token, _TOP_PROMPTS, {"limit": _PROMPT_COUNT}, bucket)
        prompts = [
            p
            for p in ((data or {}).get("prompts") or [])
            if isinstance(p, dict) and isinstance(p.get("id"), int) and isinstance(p.get("question"), str)
        ]
        if not prompts:
            return []
        ids = [p["id"] for p in prompts]
        data = await hardcover_graphql(
            http, token, _PROMPT_BOOKS, {"ids": ids, "per": _BOOKS_PER_PROMPT}, bucket
        )
    except HardcoverRateLimited:
        return []
    except Exception:  # noqa: BLE001 — best-effort
        logger.exception("hardcover prompts fetch failed")
        return []
    finally:
        if owns_client:
            await http.aclose()

    books_by_prompt: dict[int, list[int]] = {}
    for row in (data or {}).get("prompts") or []:
        if not isinstance(row, dict) or not isinstance(row.get("id"), int):
            continue
        ids = [
            pb["book_id"]
            for pb in row.get("prompt_books") or []
            if isinstance(pb, dict) and isinstance(pb.get("book_id"), int)
        ]
        books_by_prompt[row["id"]] = ids

    out: list[dict] = []
    for p in prompts:
        out.append(
            {
                "id": p["id"],
                "question": p["question"].strip(),
                "slug": p.get("slug") if isinstance(p.get("slug"), str) else None,
                "bookIds": books_by_prompt.get(p["id"], []),
            }
        )
    logger.info("hardcover prompts: %d fetched", len(out))
    return out
