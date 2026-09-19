"""prompts/39 — locally-computed "similar books", from books' own local-LLM
tags (`Book.llm_tags_json.full`, once done) rather than Hardcover's crowd
data. Weighted cosine similarity over a multi-hot genres/moods/
representation vector.

Themes are deliberately excluded from scoring (see the note by `_WEIGHTS`
below) — an exact theme match, when one happens to occur, still gets
recorded in a result's `sharedTags` for free, just not used to rank.
Content-warning tags are excluded entirely: they're a flag, not a
similarity signal, and including them would create the same "hub" false-
similarity problem `prompts/39-llm-tag-intelligence.md`'s libtrails
deep-dive documents (a generic warning like "graphic violence" would
falsely link huge swathes of unrelated books).

Tag weights are scaled by a per-run IDF (inverse document frequency) over
the eligible-book pool, so a tag that happens to be near-universal in
*this* library (e.g. "Fantasy") doesn't dominate every match the way a raw
shared-tag count would — self-adjusting, no stoplist to maintain.

Content-similarity is relational: a newly-tagged book can change another
book's top list even though that other book's own tags never changed. So
unlike `hardcover_recs_service`, there's no per-book staleness cursor —
every run recomputes the whole thing. Cheap at BookBrain's scale (a few
thousand books, a few hundred distinct tag values) done as one matrix
multiply rather than a nested loop.
"""

import logging
import math
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import Book, File, FileStatus
from app.services.library_index_service import _llm_tags

logger = logging.getLogger(__name__)

# Category weight before IDF scaling. No "themes" (the LLM rephrases the
# same idea differently almost every time — "power and control" vs "power
# and corruption" — so exact-match scoring on them is closer to noise than
# signal; a proper fix needs fuzzy dedup, deferred) and no
# "contentWarnings" (a flag, not a similarity signal).
_WEIGHTS = {"genres": 3.0, "moods": 2.0, "representation": 1.0}
_KEEP = 8  # matches kept per book
_MIN_SHARED_TAGS = 2  # distinct scored tags required before a match counts
_MAX_PER_SERIES = 2  # diversity guard: cap how many results share one series


@dataclass
class _BookTags:
    book: Book
    series_id: int | None
    scored: dict[str, float]  # tag -> idf-scaled weight, the similarity vector
    themes: set[str]  # not scored — bonus-only for `sharedTags` explainability


def _raw_tag_pairs(llm_tags: dict) -> list[tuple[str, float]]:
    return [
        (value, weight)
        for category, weight in _WEIGHTS.items()
        for value in llm_tags.get(category, [])
    ]


def _build_rows(candidates: list[Book]) -> list[_BookTags]:
    df: Counter[str] = Counter()
    per_book: list[tuple[Book, list[tuple[str, float]], set[str]]] = []
    for book in candidates:
        llm_tags = _llm_tags(book.llm_tags_json)
        pairs = _raw_tag_pairs(llm_tags)
        if not pairs:
            continue
        themes = set(llm_tags.get("themes", []))
        per_book.append((book, pairs, themes))
        for tag in {t for t, _w in pairs}:
            df[tag] += 1

    n = len(per_book)
    idf = {tag: math.log((n + 1) / (count + 1)) + 1 for tag, count in df.items()}

    rows: list[_BookTags] = []
    for book, pairs, themes in per_book:
        scored: dict[str, float] = {}
        for tag, cat_weight in pairs:
            scored[tag] = max(scored.get(tag, 0.0), cat_weight * idf[tag])
        rows.append(_BookTags(book=book, series_id=book.series_id, scored=scored, themes=themes))
    return rows


def _to_matrix(rows: list[_BookTags]) -> np.ndarray:
    vocab = sorted({tag for r in rows for tag in r.scored})
    index = {tag: i for i, tag in enumerate(vocab)}
    matrix = np.zeros((len(rows), len(vocab)), dtype=np.float32)
    for i, r in enumerate(rows):
        for tag, w in r.scored.items():
            matrix[i, index[tag]] = w
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return matrix / np.clip(norms, 1e-9, None)


def _top_matches(idx: int, rows: list[_BookTags], sim: np.ndarray) -> list[dict]:
    order = np.argsort(-sim[idx])
    matches: list[dict] = []
    series_count: Counter[int] = Counter()
    source = rows[idx]
    for j in order:
        if j == idx:
            continue
        score = float(sim[idx, j])
        if score <= 0:
            break  # descending order — nothing further qualifies
        other = rows[j]
        shared_scored = set(source.scored) & set(other.scored)
        if len(shared_scored) < _MIN_SHARED_TAGS:
            continue
        if other.series_id is not None:
            if series_count[other.series_id] >= _MAX_PER_SERIES:
                continue
            series_count[other.series_id] += 1
        shared = sorted(shared_scored | (source.themes & other.themes))
        matches.append({"bookId": other.book.id, "sharedTags": shared, "score": round(score, 4)})
        if len(matches) >= _KEEP:
            break
    return matches


async def refresh_content_recs(session: AsyncSession) -> dict:
    """Recompute every eligible book's `content_recs_json` from scratch.
    Eligible = organised, with a finished local-LLM tagging pass. Never
    fails the caller — books simply keep last run's data if this errors."""
    books = (
        (
            await session.execute(
                select(Book)
                .join(File, File.book_id == Book.id)
                .where(File.status == FileStatus.organised)
                .distinct()
            )
        )
        .scalars()
        .all()
    )

    candidates = [b for b in books if _llm_tags(b.llm_tags_json)]
    rows = _build_rows(candidates)

    matches_by_book_id: dict[int, list[dict]] = {}
    if len(rows) >= 2:
        matrix = _to_matrix(rows)
        sim = matrix @ matrix.T
        for i, r in enumerate(rows):
            matches_by_book_id[r.book.id] = _top_matches(i, rows, sim)

    now = datetime.now(UTC)
    with_matches = 0
    for book in candidates:
        matches = matches_by_book_id.get(book.id, [])
        book.content_recs_json = {"similar": matches, "generatedAt": now.isoformat()}
        book.content_recs_synced_at = now
        if matches:
            with_matches += 1
    await session.commit()

    logger.info(
        "content recs: %d books scored, %d with at least one match", len(candidates), with_matches
    )
    return {"books": len(candidates), "withMatches": with_matches}
