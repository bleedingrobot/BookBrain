"""One-off maintenance: re-match series whose stored Hardcover catalog has
too few book entries to name their own gaps (same failure mode as the
Mistborn fix — an auto-match picked a stub/spin-off Hardcover series entry
instead of the real one). For each affected series, searches Hardcover fresh
and only swaps in a candidate that returns *strictly more* book entries than
what's currently stored — never makes a series worse, only better or
unchanged. Applied candidates are pinned `match: "manual"` (per
hardcover_series_service's convention) so the nightly refresh never
silently re-matches them again.

Run once, by hand: `python tools/fix_series_matches.py`
"""

import asyncio
from datetime import UTC, datetime

import httpx
from sqlalchemy import select

from app.core.config import get_settings
from app.data.db import async_session_factory
from app.data.models import Author, Book, Series
from app.providers.metadata.hardcover import _TokenBucket, hardcover_graphql
from app.services.hardcover_series_service import (
    _SERIES_SEARCH,
    _fetch_books,
    _hits,
    _match_score,
    _series_key,
)
from app.services.text_match import normalize_person_name

AFFECTED = [
    "Behold Humanity!", "Deverry", "Aeon 14: Perseus Gate Season 1", "Vampire Chronicles",
    "Black Jewels", "Undying Mercenaries", "Nameless", "Stargate Atlantis", "Depthless Hunger",
    "Forgotten Realms: The Sellswords", "Neverwinter Saga", "Forgotten Realms: Lost Gods",
    "House of Serpents", "Forgotten Realms: The Harpers", "Forgotten Realms: Druidhome Trilogy",
    "The Double Diamond Triangle Saga", "Double Diamond Triangle Saga",
    "Forgotten Realms: Baldur's Gate", "The Book of the Long Sun", "Silo", "The Dresden Files",
    "The Bartimaeus Trilogy", "Dragonriders of Pern", "The Grisha Trilogy",
    "Wings of Fire Graphic Novels", "Alice in Deadland", "Junkyard Druid Novellas",
    "The Chronicles of Shadow Bourne", "Polity", "Shadow Ops",
]


async def series_author(session, series_id: int) -> str | None:
    row = await session.execute(
        select(Book.author_id).where(Book.series_id == series_id, Book.author_id.is_not(None)).limit(1)
    )
    author_id = row.scalar_one_or_none()
    if author_id is None:
        return None
    author = await session.get(Author, author_id)
    return author.name if author else None


async def main() -> None:
    token = get_settings().hardcover_api_token
    bucket = _TokenBucket(rate_per_sec=0.9, burst=8)
    improved: list[tuple[str, int, int, str]] = []
    unchanged: list[str] = []

    async with httpx.AsyncClient(timeout=20) as client, async_session_factory() as session:
        for name in AFFECTED:
            series = (
                await session.execute(select(Series).where(Series.name == name))
            ).scalar_one_or_none()
            if series is None:
                unchanged.append(f"{name} (no Series row)")
                continue
            current = series.hardcover_json if isinstance(series.hardcover_json, dict) else {}
            current_books = len(current.get("books") or [])
            author = await series_author(session, series.id)
            author_key = normalize_person_name(author) if author else None

            query = f"{name} {author}".strip() if author else name.strip()
            data = await hardcover_graphql(client, token, _SERIES_SEARCH, {"q": query}, bucket)
            if data is None:
                unchanged.append(f"{name} (search failed)")
                continue

            bb_key = _series_key(name)
            candidates: list[tuple[float, int, str, str]] = []
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
                try:
                    hc_id = int(doc["id"])
                except (KeyError, TypeError, ValueError):
                    continue
                if hc_id == current.get("id"):
                    continue  # already have this one, and it's the problem
                candidates.append((score, hc_id, hc_name, doc.get("slug") or ""))
            candidates.sort(key=lambda c: -c[0])

            best: tuple[int, str, str, int | None, int, list] | None = None
            for _score, hc_id, hc_name, hc_slug in candidates[:4]:
                fetched = await _fetch_books(client, token, bucket, hc_id)
                await asyncio.sleep(1.2)
                if fetched is None:
                    continue
                books, primary_count, fname, fslug = fetched
                if len(books) > current_books and (best is None or len(books) > best[4]):
                    best = (hc_id, fname or hc_name, fslug or hc_slug, primary_count, len(books), books)

            if best:
                hc_id, fname, fslug, primary_count, bcount, books = best
                series.hardcover_json = {
                    "id": hc_id, "name": fname, "slug": fslug,
                    "primaryCount": primary_count, "books": books, "match": "manual",
                }
                series.hardcover_synced_at = datetime.now(UTC).replace(tzinfo=None)
                await session.commit()
                improved.append((name, current_books, bcount, fname))
            else:
                unchanged.append(name)

    print(f"\nimproved {len(improved)}:")
    for n, before, after, fname in improved:
        print(f"  {n}: {before} -> {after} book entries (now: {fname!r})")
    print(f"\nleft as-is, {len(unchanged)} (no strictly-better candidate found):")
    for n in unchanged:
        print(f"  {n}")


if __name__ == "__main__":
    asyncio.run(main())
