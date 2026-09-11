"""One-off maintenance: sweep every organised series with no Hardcover match
at all (`match: "none"` or no hardcover_json) and try to rescue it — a real
series that just got missed because BookBrain's tag differs from Hardcover's
name (a different word, not just article/case — "Time Quartet" vs Hardcover's
"Time Quintet" was the case that prompted this), or carries a numbering
prefix BookBrain added ("1. Riftwar Saga") that Hardcover's own name doesn't
have.

Safety: with a known author, a candidate is only accepted if its Hardcover
author matches exactly (the real anti-false-positive guard — author is a much
harder signal to spoof than a name). Without a known author, falls back to
the stricter name-only `_match_score` gate `_find_series_id` already uses.
Only applied when the fetched catalog has >= 2 book entries. Pinned
`match: "manual"` so the nightly refresh won't silently drop it.

Run once, by hand: `python tools/fix_unmatched_series.py`
Writes a UTF-8 report to tools/fix_unmatched_series_report.txt (the Windows
console can't print some series/author names as-is).
"""

import asyncio
import json
import re
from datetime import UTC, datetime
from pathlib import Path

import httpx
from sqlalchemy import select

from app.core.config import get_settings
from app.data.db import async_session_factory
from app.data.models import Author, Book, File, FileStatus, Series
from app.providers.metadata.hardcover import _TokenBucket, hardcover_graphql
from app.services.hardcover_series_service import (
    _SERIES_SEARCH,
    _fetch_books,
    _find_series_id,
    _hits,
    _match_score,
    _series_key,
)
from app.services.text_match import normalize_person_name

REPORT_PATH = Path(__file__).parent / "fix_unmatched_series_report.txt"
_PREFIX_RE = re.compile(r"^(?:act\s*\d+\s*:\s*|\d+\.\s*)", re.IGNORECASE)


async def unmatched_series(session) -> list[tuple[Series, str | None, int]]:
    rows = (
        await session.execute(
            select(Series.id, Series.name, Series.hardcover_json, Author.name)
            .join(Book, Book.series_id == Series.id)
            .join(File, File.book_id == Book.id)
            .join(Author, Author.id == Book.author_id, isouter=True)
            .where(File.status == FileStatus.organised, Series.name.is_not(None))
        )
    ).all()
    info: dict[int, dict] = {}
    for sid, name, hc, author in rows:
        d = info.setdefault(sid, {"name": name, "authors": set(), "count": 0, "hc": hc})
        d["authors"].add(author)
        d["count"] += 1

    out = []
    for sid, d in info.items():
        if d["count"] < 2:
            continue
        hc = d["hc"] if isinstance(d["hc"], dict) else (json.loads(d["hc"]) if d["hc"] else None)
        if hc is not None and hc.get("match") != "none":
            continue
        series = await session.get(Series, sid)
        authors = [a for a in d["authors"] if a]
        out.append((series, authors[0] if len(authors) == 1 else None, d["count"]))
    out.sort(key=lambda x: -x[2])
    return out


async def best_candidate(
    client: httpx.AsyncClient, token: str, bucket: _TokenBucket, name: str, author: str | None
) -> tuple[int, str, str] | None:
    """Author-match is the real safety net here — with a known author, accept
    the best-scoring name (even a loose one); without one, fall back to the
    stricter existing gate."""
    stripped = _PREFIX_RE.sub("", name).strip()
    queries = {name, stripped} if stripped != name else {name}

    if author:
        author_key = normalize_person_name(author)
        best: tuple[float, dict] | None = None
        for q in queries:
            query = f"{q} {author}".strip()
            data = await hardcover_graphql(client, token, _SERIES_SEARCH, {"q": query}, bucket)
            await asyncio.sleep(1.1)
            if data is None:
                continue
            bb_key = _series_key(stripped)
            for doc in _hits(data):
                hc_name = doc.get("name")
                if not isinstance(hc_name, str):
                    continue
                hc_author = doc.get("author_name") or ""
                if normalize_person_name(hc_author) != author_key:
                    continue
                # Author already matched — score is just a tiebreak among an
                # author's own series, floor it instead of rejecting on it.
                score = _match_score(bb_key, _series_key(hc_name)) or 0.1
                if best is None or score > best[0]:
                    best = (score, doc)
        if best is None:
            return None
        doc = best[1]
        try:
            return int(doc["id"]), doc["name"], doc.get("slug") or ""
        except (KeyError, TypeError, ValueError):
            return None

    # No known author — use the normal, stricter matcher, tried against both
    # the raw and prefix-stripped name.
    for q in queries:
        found = await _find_series_id(client, token, bucket, q, None)
        await asyncio.sleep(1.1)
        if found:
            return found
    return None


async def main() -> None:
    token = get_settings().hardcover_api_token
    bucket = _TokenBucket(rate_per_sec=0.9, burst=8)
    rescued: list[tuple[str, str, int]] = []
    left: list[str] = []

    async with httpx.AsyncClient(timeout=20) as client, async_session_factory() as session:
        targets = await unmatched_series(session)
        for series, author, owned_count in targets:
            name = series.name
            try:
                cand = await best_candidate(client, token, bucket, name, author)
                if cand is None:
                    left.append(f"{name}  (no acceptable candidate)")
                    continue
                hc_id, hc_name, hc_slug = cand
                fetched = await _fetch_books(client, token, bucket, hc_id)
                await asyncio.sleep(1.1)
                if fetched is None:
                    left.append(f"{name}  (matched {hc_name!r} but book fetch failed)")
                    continue
                books, primary_count, fname, fslug = fetched
                if len(books) < 2:
                    left.append(f"{name}  (matched {hc_name!r} but only {len(books)} book entries)")
                    continue
                series.hardcover_json = {
                    "id": hc_id, "name": fname or hc_name, "slug": fslug or hc_slug,
                    "primaryCount": primary_count, "books": books, "match": "manual",
                }
                series.hardcover_synced_at = datetime.now(UTC).replace(tzinfo=None)
                await session.commit()
                rescued.append((name, fname or hc_name, len(books)))
            except Exception as exc:  # noqa: BLE001 — one bad series must not stop the sweep
                left.append(f"{name}  (error: {exc})")
                await session.rollback()

    lines = [f"rescued {len(rescued)} of {len(rescued) + len(left)}:", ""]
    for name, hc_name, n in rescued:
        lines.append(f"  {name}  ->  {hc_name}  ({n} book entries)")
    lines.append("")
    lines.append(f"left unmatched, {len(left)}:")
    for entry in left:
        lines.append(f"  {entry}")
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"done — {len(rescued)} rescued, {len(left)} left. Report: {REPORT_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
