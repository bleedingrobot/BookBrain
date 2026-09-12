"""One-off maintenance, round 3: re-sweep every series that still shows a
"bare" missing entry in the viewer (a `#N` with no title/Request button) —
found via a live audit of the actual bookbrain-index.json + seriesGaps.ts
logic on 2026-09-11, prompted by James: "seems lots with no request buttons".

That audit found 80 of 154 missing slots across 70 gapped series were bare,
in two flavours:
  1. genuinely unmatched (`match: "none"`/null, or matched-but-books:0) — 25
     series, same class the earlier `fix_unmatched_series.py` targeted.
  2. matched to a catalog that's *missing specific positions* even though
     it has some book data — 45 series, `fix_series_matches.py`'s class.

Digging into WHY several of round 2's own AFFECTED series (Dragonriders of
Pern, Stargate Atlantis) never actually improved despite being on its list:
`fix_series_matches.py`'s `series_author` picks ONE author row with no
ordering — for Dragonriders that's `Todd McCaffrey` (1 of 14 owned books) not
`Anne McCaffrey` (13 of 14) — and then hard-*rejects* any Hardcover candidate
whose author doesn't match that pick. Search results plainly showed the
correct, populated "Dragonriders of Pern" by Anne McCaffrey (id 1027, 6 book
entries) right next to the empty "Todd McCaffrey" stub (id 280582, 0 entries,
the one already stored) — but the author gate threw it out before it was ever
considered. Stargate Atlantis is worse: the correctly-identified majority
author (Martha Wells, her only 2 owned books) IS Hardcover's real credited
author for a near-empty stub — the complete 24-book franchise series is
credited to a different name (Sally Malcolm, an editor/compiler credit) that
no per-owned-book author will ever match.

Fix here: author is a *scoring* signal, never a hard filter — a candidate
whose Hardcover author doesn't match the (correctly computed, plurality —
not arbitrary) series author can still win if it has real book data. The
actual safety net against a wrong-but-populated candidate (the false-positive
class round 2 caught separately) is instead a **mandatory title-overlap
check**: never accept a candidate unless at least one of its book titles
matches (exact or substring, normalised) a title we actually own in that
series. That's a much harder signal to spoof than an author name string, and
it's applied inline here rather than as an after-the-fact pass.

Only swaps in a candidate with MORE book entries than what's currently
stored — never makes a series worse. Pins `match: "manual"`.

Run once, by hand: `python tools/fix_series_gaps_v2.py`
Writes a UTF-8 report to tools/fix_series_gaps_v2_report.txt.
"""

import asyncio
import json
import re
from collections import Counter
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
    _hits,
    _match_score,
    _series_key,
)
from app.services.text_match import normalize_person_name, normalize_title

REPORT_PATH = Path(__file__).parent / "fix_series_gaps_v2_report.txt"
_PREFIX_RE = re.compile(r"^(?:act\s*\d+\s*:\s*|\d+\.\s*)", re.IGNORECASE)


async def gapped_series(session) -> list[tuple[Series, str | None, set[int], list[str]]]:
    """Every organised series with 2+ owned whole numbers and at least one
    number missing below the highest owned — mirrors seriesGaps.ts's own
    definition closely enough for matching purposes."""
    rows = (
        await session.execute(
            select(Series.id, Series.name, Series.hardcover_json, Book.series_number, Book.canonical_title, Author.name)
            .join(Book, Book.series_id == Series.id)
            .join(File, File.book_id == Book.id)
            .join(Author, Author.id == Book.author_id, isouter=True)
            .where(File.status == FileStatus.organised, Series.name.is_not(None))
        )
    ).all()
    info: dict[int, dict] = {}
    for sid, name, hc, num, title, author in rows:
        d = info.setdefault(sid, {"name": name, "hc": hc, "owned": set(), "titles": [], "authors": Counter()})
        if num is not None:
            try:
                n = float(num)
                if n.is_integer():
                    d["owned"].add(int(n))
            except (TypeError, ValueError):
                pass
        if title:
            d["titles"].append(title)
        if author:
            d["authors"][author] += 1

    out = []
    for sid, d in info.items():
        owned = d["owned"]
        if len(owned) < 2:
            continue
        max_owned = max(owned)
        missing = [i for i in range(1, max_owned) if i not in owned]
        if not missing:
            continue
        hc = d["hc"] if isinstance(d["hc"], dict) else (json.loads(d["hc"]) if d["hc"] else None)
        cat_positions = set()
        if hc and hc.get("books"):
            for b in hc["books"]:
                pos = b.get("position")
                if isinstance(pos, (int, float)) and float(pos).is_integer():
                    cat_positions.add(int(pos))
        bare = [m for m in missing if m not in cat_positions]
        if not bare:
            continue  # every missing slot already has a title — nothing to fix
        plurality_author = d["authors"].most_common(1)[0][0] if d["authors"] else None
        series = await session.get(Series, sid)
        out.append((series, plurality_author, owned, d["titles"]))
    out.sort(key=lambda x: -len(x[2]))
    return out


def _norm(s: str) -> str:
    return normalize_title(s) if s else ""


def title_overlap(owned_titles: list[str], candidate_books: list[dict]) -> bool:
    owned_norm = {_norm(t) for t in owned_titles if t}
    for b in candidate_books:
        t = _norm(b.get("title") or "")
        if not t:
            continue
        if t in owned_norm:
            return True
        # substring either direction catches subtitle/edition noise
        if any(t in o or o in t for o in owned_norm if len(o) > 4 and len(t) > 4):
            return True
    return False


async def best_candidate(
    client: httpx.AsyncClient,
    token: str,
    bucket: _TokenBucket,
    name: str,
    author: str | None,
    owned_titles: list[str],
    current_books: int,
) -> tuple[int, str, str, int | None, list] | None:
    stripped = _PREFIX_RE.sub("", name).strip()
    queries = [name] if stripped == name else [name, stripped]
    author_key = normalize_person_name(author) if author else None
    bb_key = _series_key(stripped)

    pool: dict[int, tuple[float, bool, str, str]] = {}  # id -> (score, author_match, name, slug)
    for q in queries:
        data = await hardcover_graphql(client, token, _SERIES_SEARCH, {"q": q}, bucket)
        await asyncio.sleep(1.1)
        if data is None:
            continue
        for doc in _hits(data):
            hc_name = doc.get("name")
            if not isinstance(hc_name, str):
                continue
            score = _match_score(bb_key, _series_key(hc_name))
            if score is None:
                continue
            try:
                hc_id = int(doc["id"])
            except (KeyError, TypeError, ValueError):
                continue
            author_match = bool(author_key) and normalize_person_name(doc.get("author_name") or "") == author_key
            prev = pool.get(hc_id)
            if prev is None or (score, author_match) > (prev[0], prev[1]):
                pool[hc_id] = (score, author_match, doc.get("name"), doc.get("slug") or "")

    # Author-matching candidates first (more trustworthy), then by score —
    # but every candidate gets a fetch-and-verify shot, not just the top one.
    ranked = sorted(pool.items(), key=lambda kv: (-kv[1][1], -kv[1][0]))[:6]

    for hc_id, (_score, _author_match, hc_name, hc_slug) in ranked:
        fetched = await _fetch_books(client, token, bucket, hc_id)
        await asyncio.sleep(1.1)
        if fetched is None:
            continue
        books, primary_count, fname, fslug = fetched
        if len(books) <= current_books:
            continue
        if not title_overlap(owned_titles, books):
            continue  # real safety net — reject a populated-but-wrong series
        return hc_id, fname or hc_name, fslug or hc_slug, primary_count, books
    return None


async def main() -> None:
    token = get_settings().hardcover_api_token
    bucket = _TokenBucket(rate_per_sec=0.9, burst=8)
    improved: list[tuple[str, int, int, str]] = []
    unchanged: list[str] = []

    async with httpx.AsyncClient(timeout=20) as client, async_session_factory() as session:
        targets = await gapped_series(session)
        for series, author, owned, titles in targets:
            current = series.hardcover_json if isinstance(series.hardcover_json, dict) else {}
            current_books = len(current.get("books") or [])
            try:
                cand = await best_candidate(client, token, bucket, series.name, author, titles, current_books)
                if cand is None:
                    unchanged.append(f"{series.name}  (no strictly-better, title-verified candidate)")
                    continue
                hc_id, fname, fslug, primary_count, books = cand
                series.hardcover_json = {
                    "id": hc_id, "name": fname, "slug": fslug,
                    "primaryCount": primary_count, "books": books, "match": "manual",
                }
                series.hardcover_synced_at = datetime.now(UTC).replace(tzinfo=None)
                await session.commit()
                improved.append((series.name, current_books, len(books), fname))
            except Exception as exc:  # noqa: BLE001 — one bad series must not stop the sweep
                unchanged.append(f"{series.name}  (error: {exc})")
                await session.rollback()

    lines = [f"improved {len(improved)} of {len(improved) + len(unchanged)}:", ""]
    for name, before, after, fname in improved:
        lines.append(f"  {name}: {before} -> {after} book entries (now: {fname!r})")
    lines.append("")
    lines.append(f"left as-is, {len(unchanged)}:")
    for entry in unchanged:
        lines.append(f"  {entry}")
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"done — {len(improved)} improved, {len(unchanged)} left. Report: {REPORT_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
