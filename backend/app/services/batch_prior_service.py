"""prompts/15 Stage K — batch priors.

A pile of one author's (or one series') books landing in a single scan is a
strong signal that the *thin* files in the same batch, whose filenames point at
that same author/series, are probably right too — but today every file is
identified in complete isolation.

After a scan batch finishes identifying and before the auto-organize pass, this
looks for a batch consensus (>= 3 confidently-identified files sharing an
author or series) and, for a low-confidence file in the same batch whose
filename names that author/series:

* if the file's own identification agrees with the consensus -> a small,
  logged confidence bump; if that clears the auto bar it moves from `review`
  to `inbox` (and its pending Review is dropped);
* if it disagrees -> left in `review`, with an explanatory note so the
  reviewer sees the batch context.

It never rewrites a title/author/series — only the confidence + routing, and
every change is recorded under ``ai_decisions.raw_response_json["batch_prior"]``.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.data.models import AIDecision, Book, File, FileStatus, Review, ReviewStatus
from app.services.text_match import normalize, normalize_person_name, normalize_words

logger = logging.getLogger(__name__)

_MIN_CONSENSUS = 3
_BATCH_PRIOR_BONUS = 12
# never let a batch prior alone push a file to the top (>=95) auto-organize
# tier — it's a supporting signal, not a primary one.
_BATCH_PRIOR_CEILING = 92
_ARTICLES = frozenset({"the", "a", "an"})


@dataclass
class BatchPriorAdjustment:
    file_id: int
    filename: str
    kind: str  # "bumped" | "conflict"
    consensus: str
    old_confidence: int
    new_confidence: int


def _series_key(name: str | None) -> str:
    return "".join(sorted(normalize_words(name) - _ARTICLES))


async def apply_batch_priors(
    session: AsyncSession, drive_file_ids: list[str]
) -> list[BatchPriorAdjustment]:
    if len(drive_file_ids) <= _MIN_CONSENSUS:
        return []

    files = (
        (
            await session.execute(
                select(File)
                .where(File.drive_file_id.in_(drive_file_ids))
                .where(File.status.in_([FileStatus.inbox, FileStatus.review]))
                .options(
                    selectinload(File.book).selectinload(Book.author),
                    selectinload(File.book).selectinload(Book.series),
                )
            )
        )
        .scalars()
        .all()
    )
    if len(files) <= _MIN_CONSENSUS:
        return []

    latest_decision = await _latest_decisions(session, [f.id for f in files])

    # --- build the consensus from the confident, auto-eligible files ---------
    settings = get_settings()
    author_votes: Counter[str] = Counter()
    series_votes: Counter[str] = Counter()
    author_display: dict[str, str] = {}
    series_display: dict[str, str] = {}
    for f in files:
        dec = latest_decision.get(f.id)
        if (
            f.status != FileStatus.inbox
            or dec is None
            or dec.computed_confidence < settings.confidence_auto_flagged
            or f.book is None
        ):
            continue
        if f.book.author:
            k = normalize_person_name(f.book.author.name)
            if k:
                author_votes[k] += 1
                author_display.setdefault(k, f.book.author.name)
        if f.book.series:
            k = _series_key(f.book.series.name)
            if k:
                series_votes[k] += 1
                series_display.setdefault(k, f.book.series.name)

    consensus_authors = {k for k, n in author_votes.items() if n >= _MIN_CONSENSUS}
    consensus_series = {k for k, n in series_votes.items() if n >= _MIN_CONSENSUS}
    if not consensus_authors and not consensus_series:
        return []

    # --- re-score the low-confidence files ---------------------------------
    adjustments: list[BatchPriorAdjustment] = []
    for f in files:
        if f.status != FileStatus.review:
            continue
        dec = latest_decision.get(f.id)
        if dec is None or "batch_prior" in (dec.raw_response_json or {}):
            continue

        fname = normalize(f.filename)
        author_hit = next(
            (k for k in consensus_authors if normalize(author_display[k]) in fname), None
        )
        series_hit = next(
            (k for k in consensus_series if normalize(series_display[k]) in fname), None
        )
        if author_hit is None and series_hit is None:
            continue

        own_author = normalize_person_name(f.book.author.name) if f.book and f.book.author else None
        own_series = _series_key(f.book.series.name) if f.book and f.book.series else None
        agrees = (author_hit is not None and own_author == author_hit) or (
            series_hit is not None and own_series == series_hit
        )
        consensus_name = author_display.get(author_hit or "") or series_display.get(series_hit or "")

        raw = dict(dec.raw_response_json or {})
        old = dec.computed_confidence
        if agrees:
            new = min(old + _BATCH_PRIOR_BONUS, _BATCH_PRIOR_CEILING)
            raw["batch_prior"] = {
                "kind": "bumped",
                "consensus": consensus_name,
                "from": old,
                "to": new,
                "note": (
                    f"{_MIN_CONSENSUS}+ files in this scan resolved to {consensus_name!r}, "
                    f"and this filename names it — confidence raised {old} -> {new}."
                ),
            }
            dec.raw_response_json = raw
            dec.computed_confidence = new
            kind = "bumped"
            if new >= settings.confidence_auto_flagged:
                f.status = FileStatus.inbox
                f.status_reason = None
                await _drop_pending_review(session, f.id)
        else:
            new = old
            raw["batch_prior"] = {
                "kind": "conflict",
                "consensus": consensus_name,
                "note": (
                    f"{_MIN_CONSENSUS}+ files in this scan resolved to {consensus_name!r} and "
                    f"this filename names it, but this file was identified as something else — "
                    "left in review."
                ),
            }
            dec.raw_response_json = raw
            kind = "conflict"

        adjustments.append(
            BatchPriorAdjustment(
                file_id=f.id,
                filename=f.filename,
                kind=kind,
                consensus=consensus_name,
                old_confidence=old,
                new_confidence=new,
            )
        )

    if adjustments:
        await session.commit()
        logger.info(
            "batch priors: %d bumped, %d conflicts",
            sum(a.kind == "bumped" for a in adjustments),
            sum(a.kind == "conflict" for a in adjustments),
        )
    return adjustments


async def _latest_decisions(session: AsyncSession, file_ids: list[int]) -> dict[int, AIDecision]:
    rows = (
        (
            await session.execute(
                select(AIDecision).where(AIDecision.file_id.in_(file_ids)).order_by(AIDecision.id)
            )
        )
        .scalars()
        .all()
    )
    latest: dict[int, AIDecision] = {}
    for row in rows:  # ascending id -> last write wins
        latest[row.file_id] = row
    return latest


async def _drop_pending_review(session: AsyncSession, file_id: int) -> None:
    review = (
        await session.execute(
            select(Review).where(
                Review.file_id == file_id, Review.status == ReviewStatus.pending
            )
        )
    ).scalar_one_or_none()
    if review is not None:
        await session.delete(review)
