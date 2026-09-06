"""Bulk Re-identify Audit (prompts/05).

A read-only report that re-checks every organised book's stored identification
against current data and lists the ones where re-identification now disagrees.
Different failure mode from Library Audit (task 1): that one compares DB row
*names*; this one re-derives what identification *would* say and diffs it.

Cost control is the point (SPEC §1, prompts/05 "Cost control"):

- The default pass makes **zero** Anthropic calls. It reuses the cached
  `ai_decisions` rows (never regenerates them), reconstructs the EPUB evidence
  and candidates from what the scan already stored, recomputes the deterministic
  `confidence_service` score, and does free Google Books / Open Library lookups.
- The AI is consulted only for a bounded, opt-in "deep re-check" of rows the
  free pass already flagged — capped, with a credit estimate shown first.
- The report itself is expensive to build (a provider lookup per book), so it's
  cached as a JSON blob in `settings` with a `generated_at` stamp and only
  rebuilt on demand — mirroring the nightly `job_runs` trail.

Never moves a file or writes a book row — it only reports. Acting on a flagged
book is the existing `/correct` flow or a series merge.
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.settings_keys import REIDENT_REPORT_JSON
from app.data.db import async_session_factory
from app.data.models import (
    AIDecision,
    Book,
    BookCandidate,
    DismissedReidentFlag,
    File,
    FileStatus,
    Identifier,
    IdentifierType,
    LibraryRule,
    MetadataSource,
    Review,
    ReviewStatus,
    RuleType,
)
from app.data.repositories.settings_repository import SettingsRepository
from app.providers.ai.anthropic_client import AIIdentificationError, AnthropicIdentificationClient
from app.providers.epub.parser import EpubEvidence
from app.providers.metadata.types import MetadataCandidate
from app.schemas.reident_audit import (
    DeepCheckEstimate,
    DeepCheckResult,
    DeepCheckRow,
    ReidentDismissedInfo,
    ReidentDivergence,
    ReidentRebuildJobState,
    ReidentRebuildJobStatus,
    ReidentReport,
    ReidentSignal,
)
from app.services.candidate_service import CandidateService, default_candidate_service
from app.services.confidence_service import score
from app.services.text_match import normalize, normalize_title, normalize_words, titles_match

logger = logging.getLogger(__name__)

# Deterministic identification paths — an AIDecision with one of these models
# didn't get its title/series from the model's imagination, so the
# "AI invented the series" signal doesn't apply and ai_corroborates is off.
_DETERMINISTIC_MODELS = {None, "deterministic", "unavailable", "library_rule"}

# Provider HTTP concurrency for the rebuild pass. Same shape as
# cover_service._COVER_CONCURRENCY — bounded so a full run doesn't hammer
# Google Books into a 429 storm (which is treated as "no data", not
# "everything diverged" — see prompts/05 gotchas).
_HTTP_CONCURRENCY = 4

# Deep re-check: hard cap per run and a padded per-row credit estimate
# (claude-opus-5, ~1.5k in + ~0.4k out per call).
DEEP_CHECK_CAP = 50
_DEEP_CHECK_CONCURRENCY = 3
_DEEP_CHECK_USD_PER_ROW = 0.02


# --------------------------------------------------------------------------
# Reconstructing what identification saw, from what the scan stored
# --------------------------------------------------------------------------

_EVIDENCE_SOURCES = {"epub", "comic"}


def evidence_from_sources(rows: list[MetadataSource]) -> EpubEvidence:
    """Rebuild the EpubEvidence a file was identified from, out of its stored
    metadata_sources rows. The inverse of scan_service._evidence_to_metadata_sources
    — kept faithful to it so a recomputed confidence score (and evidence hash)
    matches what the pipeline actually had. See the evidence_hash stability
    test in tests/test_reident_audit_service.py."""
    by_field: dict[str, str] = {}
    for row in rows:
        if row.source in _EVIDENCE_SOURCES:
            by_field.setdefault(row.field_name, row.value)

    series_number: float | None = None
    if "series_number" in by_field:
        try:
            series_number = float(by_field["series_number"])
        except (TypeError, ValueError):
            series_number = None

    return EpubEvidence(
        title=by_field.get("title"),
        authors=by_field["authors"].split(", ") if by_field.get("authors") else [],
        language=by_field.get("language"),
        description=by_field.get("description"),
        isbn10=by_field.get("isbn10"),
        isbn13=by_field.get("isbn13"),
        series=by_field.get("series"),
        series_number=series_number,
        text_snippet=by_field.get("text_snippet", ""),
    )


def candidates_from_rows(rows: list[BookCandidate]) -> list[MetadataCandidate]:
    # The `filename` pseudo-candidate (prompts/15 Stage C) is a heuristic parse,
    # not a metadata provider — it must not enter the provider-consensus /
    # disagreement maths here.
    return [
        MetadataCandidate(
            title=r.title,
            authors=r.author.split(", ") if r.author else [],
            series=r.series,
            series_number=r.series_number,
            source=r.source,
        )
        for r in rows
        if r.source != "filename"
    ]


# --------------------------------------------------------------------------
# Report building
# --------------------------------------------------------------------------


@dataclass
class _BookRow:
    book_id: int
    file_id: int
    filename: str
    stored_title: str
    stored_author: str | None
    stored_series: str | None
    stored_series_number: float | None
    isbn13: str | None
    isbn10: str | None
    evidence: EpubEvidence
    stored_candidates: list[MetadataCandidate]
    stored_confidence: int | None
    ai_model: str | None
    stored_from_human: bool
    # filled during the HTTP phase
    fresh: list[MetadataCandidate] = field(default_factory=list)
    isbn_fresh: list[MetadataCandidate] = field(default_factory=list)
    provider_ok: bool = True


def _latest_decision(decisions: list[AIDecision]) -> AIDecision | None:
    return max(decisions, key=lambda d: d.id) if decisions else None


def _pick_primary(files: list[File]) -> File:
    # Same tie-break as duplicate_service.detect_same_book_duplicates: best
    # quality, then first discovered.
    return sorted(files, key=lambda f: (-(f.quality_score or 0), f.discovered_at, f.id))[0]


async def _load_book_rows(session: AsyncSession) -> list[_BookRow]:
    files = (
        (
            await session.execute(
                select(File)
                .where(File.status == FileStatus.organised, File.book_id.is_not(None))
                .options(
                    selectinload(File.book).selectinload(Book.author),
                    selectinload(File.book).selectinload(Book.series),
                    selectinload(File.ai_decisions),
                )
            )
        )
        .scalars()
        .all()
    )
    if not files:
        return []

    by_book: dict[int, list[File]] = {}
    for f in files:
        by_book.setdefault(f.book_id, []).append(f)

    # Human-ruled books: any file with a `corrected` review, plus any series
    # that a series_alias library_rule resolves to. Loaded once, not per book.
    corrected_file_ids = set(
        (
            await session.execute(
                select(Review.file_id).where(Review.status == ReviewStatus.corrected)
            )
        )
        .scalars()
        .all()
    )
    rule_series = {
        normalize_words(r.resolution_json.get("series"))
        for r in (
            await session.execute(
                select(LibraryRule).where(LibraryRule.rule_type == RuleType.series_alias)
            )
        )
        .scalars()
        .all()
        if r.resolution_json.get("series")
    }

    all_file_ids = [f.id for f in files]
    sources_by_file: dict[int, list[MetadataSource]] = {fid: [] for fid in all_file_ids}
    for row in (
        (
            await session.execute(
                select(MetadataSource).where(MetadataSource.file_id.in_(all_file_ids))
            )
        )
        .scalars()
        .all()
    ):
        sources_by_file.setdefault(row.file_id, []).append(row)

    cands_by_file: dict[int, list[BookCandidate]] = {fid: [] for fid in all_file_ids}
    for row in (
        (
            await session.execute(
                select(BookCandidate).where(BookCandidate.file_id.in_(all_file_ids))
            )
        )
        .scalars()
        .all()
    ):
        cands_by_file.setdefault(row.file_id, []).append(row)

    book_ids = list(by_book)
    isbns_by_book: dict[int, tuple[str | None, str | None]] = {bid: (None, None) for bid in book_ids}
    for row in (
        (await session.execute(select(Identifier).where(Identifier.book_id.in_(book_ids))))
        .scalars()
        .all()
    ):
        isbn13, isbn10 = isbns_by_book[row.book_id]
        if row.type == IdentifierType.isbn13:
            isbn13 = row.value
        elif row.type == IdentifierType.isbn10:
            isbn10 = row.value
        isbns_by_book[row.book_id] = (isbn13, isbn10)

    rows: list[_BookRow] = []
    for book_id, book_files in by_book.items():
        primary = _pick_primary(book_files)
        book = primary.book
        decision = _latest_decision(primary.ai_decisions)
        stored_series = book.series.name if book.series else None
        from_human = any(f.id in corrected_file_ids for f in book_files) or (
            stored_series is not None and normalize_words(stored_series) in rule_series
        )
        isbn13, isbn10 = isbns_by_book[book_id]
        rows.append(
            _BookRow(
                book_id=book_id,
                file_id=primary.id,
                filename=primary.filename,
                stored_title=book.canonical_title,
                stored_author=book.author.name if book.author else None,
                stored_series=stored_series,
                stored_series_number=book.series_number,
                isbn13=isbn13,
                isbn10=isbn10,
                evidence=evidence_from_sources(sources_by_file.get(primary.id, [])),
                stored_candidates=candidates_from_rows(cands_by_file.get(primary.id, [])),
                stored_confidence=decision.computed_confidence if decision else None,
                ai_model=decision.model if decision else None,
                stored_from_human=from_human,
            )
        )
    return rows


async def _enrich(row: _BookRow, cs: CandidateService, sem: asyncio.Semaphore) -> None:
    async with sem:
        try:
            row.fresh = await cs.generate_candidates(
                title=row.stored_title, authors=row.stored_author
            )
            if row.isbn13 or row.isbn10:
                row.isbn_fresh = await cs.generate_candidates(
                    isbn13=row.isbn13, isbn10=row.isbn10
                )
        except Exception:  # a flaky provider must not sink the whole run
            logger.exception("reident: provider lookup failed for book %s", row.book_id)
            row.provider_ok = False


def _recompute_confidence(row: _BookRow) -> int:
    real_ai = row.ai_model not in _DETERMINISTIC_MODELS
    ai_corroborates = real_ai and (
        titles_match(row.stored_title, row.evidence.title)
        or any(titles_match(row.stored_title, c.title) for c in row.stored_candidates)
    )
    return score(
        evidence=row.evidence,
        candidates=row.stored_candidates,
        filename=row.filename,
        ai_corroborates=ai_corroborates,
    ).total


def _consensus(values: list[str]) -> str | None:
    """The single normalized value every provider agrees on, or None if they
    don't agree (or there's nothing to go on)."""
    distinct = {v for v in values if v}
    return next(iter(distinct)) if len(distinct) == 1 else None


def _series_corroborated(row: _BookRow) -> bool:
    target = normalize_words(row.stored_series)
    if row.evidence.series and normalize_words(row.evidence.series) == target:
        return True
    pools = [row.stored_candidates, row.fresh, row.isbn_fresh]
    return any(
        c.series and normalize_words(c.series) == target for pool in pools for c in pool
    )


def _divergence_for(row: _BookRow, duplicate_of: int | None) -> ReidentDivergence | None:
    signals: list[ReidentSignal] = []
    evidence: list[str] = []
    recomputed = _recompute_confidence(row)

    settings = get_settings()

    # --- Provider consensus on title / author -----------------------------
    if row.provider_ok and len(row.fresh) >= 2 and not row.stored_from_human:
        title_consensus = _consensus([normalize_title(c.title) for c in row.fresh])
        if title_consensus and title_consensus != normalize_title(row.stored_title):
            example = next(
                c.title for c in row.fresh if normalize_title(c.title) == title_consensus
            )
            signals.append(ReidentSignal.title_disagrees)
            evidence.append(
                f'Every provider now returns the title "{example}", not the stored '
                f'"{row.stored_title}".'
            )

        if row.stored_author:
            author_consensus = _consensus(
                [normalize(c.authors[0]) for c in row.fresh if c.authors]
            )
            if author_consensus and author_consensus != normalize(row.stored_author):
                example = next(
                    c.authors[0]
                    for c in row.fresh
                    if c.authors and normalize(c.authors[0]) == author_consensus
                )
                signals.append(ReidentSignal.author_disagrees)
                evidence.append(
                    f'Every provider now lists the author as "{example}", not the stored '
                    f'"{row.stored_author}".'
                )

    # --- Stored ISBN now resolves to a different work --------------------
    if row.provider_ok and (row.isbn13 or row.isbn10) and row.isbn_fresh:
        isbn = row.isbn13 or row.isbn10
        if not any(titles_match(c.title, row.stored_title) for c in row.isbn_fresh):
            other = next((c.title for c in row.isbn_fresh if c.title), "(unknown)")
            signals.append(ReidentSignal.isbn_points_elsewhere)
            evidence.append(
                f'ISBN {isbn} now resolves to "{other}", not the stored "{row.stored_title}".'
            )

    # --- Stored series looks invented -----------------------------------
    if row.stored_series and not row.stored_from_human and not _series_corroborated(row):
        real_ai = row.ai_model not in _DETERMINISTIC_MODELS
        # Only call it likely-invented when a real model supplied it and
        # nothing (EPUB, stored candidates, providers now) backs it up.
        if real_ai or row.evidence.series is None:
            signals.append(ReidentSignal.series_unverified)
            evidence.append(
                f'Stored series "{row.stored_series}"'
                + (f' #{row.stored_series_number}' if row.stored_series_number is not None else "")
                + " isn't in the EPUB, any stored candidate, or any provider now — "
                "likely an AI guess."
            )

    # --- Confidence below the auto-organize bar -------------------------
    # The stored computed_confidence is authoritative (SPEC §1) — it's the
    # number the pipeline actually routed on. The deterministic recompute is
    # only a cross-check for display: for an AI-path book it's legitimately
    # low (that's why the AI was called), so it must never drive the flag.
    effective = row.stored_confidence if row.stored_confidence is not None else recomputed
    if effective is not None and effective < settings.confidence_auto_flagged:
        signals.append(ReidentSignal.below_auto_organize)
        detail = f"App-computed confidence is {effective}"
        if row.stored_confidence is None:
            detail += " (recomputed — no stored decision)"
        evidence.append(
            detail
            + f" — below the auto bar of {settings.confidence_auto_flagged}; "
            "this should be in the review queue, not the library."
        )

    # --- Missed duplicate ---------------------------------------------
    if duplicate_of is not None:
        signals.append(ReidentSignal.possible_duplicate)
        evidence.append(
            f"Resolves to the same title + author as book #{duplicate_of} — "
            "one of them is a missed duplicate."
        )

    if not signals:
        return None

    return ReidentDivergence(
        book_id=row.book_id,
        file_id=row.file_id,
        filename=row.filename,
        stored_title=row.stored_title,
        stored_author=row.stored_author,
        stored_series=row.stored_series,
        stored_series_number=row.stored_series_number,
        stored_confidence=row.stored_confidence,
        stored_from_human=row.stored_from_human,
        signals=signals,
        evidence=evidence,
        recomputed_confidence=recomputed,
        duplicate_of_book_id=duplicate_of,
    )


def _duplicate_map(rows: list[_BookRow]) -> dict[int, int]:
    """book_id -> another book_id it shares a canonical (title, author)
    identity with. Only pairs where both are organised (they all are here)."""
    by_identity: dict[tuple[str, str], list[int]] = {}
    for row in rows:
        key = (normalize_title(row.stored_title), normalize(row.stored_author or ""))
        if not key[0]:
            continue
        by_identity.setdefault(key, []).append(row.book_id)

    out: dict[int, int] = {}
    for ids in by_identity.values():
        if len(ids) < 2:
            continue
        for bid in ids:
            out[bid] = next(other for other in ids if other != bid)
    return out


async def build_reident_report(
    *,
    candidate_service: CandidateService | None = None,
    on_progress=None,
) -> ReidentReport:
    """The free pass. Makes **zero** Anthropic calls — it only reads cached
    ai_decisions, reconstructs evidence, recomputes the deterministic
    confidence score, and does free provider lookups."""
    cs = candidate_service or default_candidate_service()

    async with async_session_factory() as session:
        rows = await _load_book_rows(session)

    total = len(rows)
    if on_progress:
        on_progress(0, total, 0)

    sem = asyncio.Semaphore(_HTTP_CONCURRENCY)
    done = 0

    async def one(row: _BookRow) -> None:
        nonlocal done
        await _enrich(row, cs, sem)
        done += 1
        if on_progress and (done % 25 == 0 or done == total):
            on_progress(done, total, 0)

    await asyncio.gather(*(one(r) for r in rows))

    dup_map = _duplicate_map(rows)
    divergences = [
        d
        for row in rows
        if (d := _divergence_for(row, dup_map.get(row.book_id))) is not None
    ]
    divergences.sort(key=lambda d: (-len(d.signals), d.book_id))

    return ReidentReport(
        generated_at=datetime.now(UTC).isoformat(),
        total_organised_books=total,
        checked=total,
        providers_unavailable=sum(1 for r in rows if not r.provider_ok),
        divergences=divergences,
    )


# --------------------------------------------------------------------------
# Cache + dismissals
# --------------------------------------------------------------------------


async def get_cached_report(session: AsyncSession) -> ReidentReport:
    raw = await SettingsRepository(session).get(REIDENT_REPORT_JSON)
    if not raw:
        return ReidentReport()
    try:
        return ReidentReport.model_validate_json(raw)
    except Exception:
        logger.exception("reident: stored report failed to parse — treating as empty")
        return ReidentReport()


async def save_report(session: AsyncSession, report: ReidentReport) -> None:
    await SettingsRepository(session).set(REIDENT_REPORT_JSON, report.model_dump_json())


async def get_report_filtered(session: AsyncSession) -> ReidentReport:
    """The cached report with dismissed books removed — applied at read time
    (not baked into the cache) so dismissing never needs a rebuild."""
    report = await get_cached_report(session)
    dismissed = await _dismissed_book_ids(session)
    if dismissed:
        report = report.model_copy(
            update={
                "divergences": [d for d in report.divergences if d.book_id not in dismissed]
            }
        )
    return report


async def _dismissed_book_ids(session: AsyncSession) -> set[int]:
    return set(
        (await session.execute(select(DismissedReidentFlag.book_id))).scalars().all()
    )


async def dismiss_book(session: AsyncSession, book_id: int) -> None:
    """Idempotent — dismissing an already-dismissed book is a no-op."""
    from sqlalchemy.exc import IntegrityError

    existing = (
        await session.execute(
            select(DismissedReidentFlag).where(DismissedReidentFlag.book_id == book_id)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return
    session.add(DismissedReidentFlag(book_id=book_id))
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()


async def list_dismissed(session: AsyncSession) -> list[ReidentDismissedInfo]:
    rows = (
        (
            await session.execute(
                select(DismissedReidentFlag).order_by(DismissedReidentFlag.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [
        ReidentDismissedInfo(
            book_id=r.book_id,
            created_at=r.created_at.isoformat() if r.created_at else "",
        )
        for r in rows
    ]


async def undismiss_book(session: AsyncSession, book_id: int) -> None:
    row = (
        await session.execute(
            select(DismissedReidentFlag).where(DismissedReidentFlag.book_id == book_id)
        )
    ).scalar_one_or_none()
    if row is not None:
        await session.delete(row)
        await session.commit()


# --------------------------------------------------------------------------
# Deep re-check (opt-in, capped, costs credits)
# --------------------------------------------------------------------------


def _deep_check_prompt(d: ReidentDivergence) -> str:
    lines = [
        "A book already filed in a personal ebook library is being re-checked "
        "because an automated pass thinks its stored identification may be wrong.",
        "",
        "Stored identification:",
        f"  title:  {d.stored_title}",
        f"  author: {d.stored_author or '(none)'}",
        f"  series: {d.stored_series or '(standalone)'}"
        + (f" #{d.stored_series_number}" if d.stored_series_number is not None else ""),
        f"  source file: {d.filename}",
        "",
        "Why the automated pass flagged it:",
    ]
    lines.extend(f"  - {line}" for line in d.evidence)
    lines.append("")
    lines.append(
        "Using your bibliographic knowledge, decide whether the stored "
        "identification is correct. Be especially careful about the series."
    )
    return "\n".join(lines)


async def estimate_deep_check(session: AsyncSession, book_ids: list[int]) -> DeepCheckEstimate:
    report = await get_cached_report(session)
    flagged = {d.book_id for d in report.divergences}
    eligible = len([b for b in set(book_ids) if b in flagged])
    will_check = min(eligible, DEEP_CHECK_CAP)
    return DeepCheckEstimate(
        eligible=eligible,
        will_check=will_check,
        cap=DEEP_CHECK_CAP,
        estimated_cost_usd=round(will_check * _DEEP_CHECK_USD_PER_ROW, 2),
    )


async def run_deep_check(
    session: AsyncSession,
    book_ids: list[int],
    ai_client: AnthropicIdentificationClient | None = None,
) -> DeepCheckResult:
    """Consults the AI, but only for rows the free pass already flagged, and
    only up to DEEP_CHECK_CAP of them. Writes the verdicts back onto the
    cached report (still read-only w.r.t. book rows)."""
    report = await get_cached_report(session)
    by_id = {d.book_id: d for d in report.divergences}
    targets = [by_id[b] for b in dict.fromkeys(book_ids) if b in by_id][:DEEP_CHECK_CAP]
    if not targets:
        return DeepCheckResult(
            rechecked=0, stored_is_wrong=0, stored_is_correct=0, uncertain=0, failed=0, rows=[]
        )

    client = ai_client or AnthropicIdentificationClient()
    sem = asyncio.Semaphore(_DEEP_CHECK_CONCURRENCY)
    rows: list[DeepCheckRow] = []
    failed = 0

    async def one(d: ReidentDivergence) -> None:
        nonlocal failed
        async with sem:
            try:
                result = await client.audit_book_identity(_deep_check_prompt(d))
            except AIIdentificationError as exc:
                failed += 1
                d.deep_check_verdict = "failed"
                d.deep_check_explanation = str(exc)
                return

        d.deep_check_verdict = result.verdict
        d.deep_check_explanation = result.explanation
        if result.verdict == "stored_is_wrong":
            d.deep_check_suggested_title = result.corrected_title or d.stored_title
            d.deep_check_suggested_author = result.corrected_author or d.stored_author
            d.deep_check_suggested_series = (
                None if not result.series_is_real else result.corrected_series
            )
            d.deep_check_suggested_series_number = result.corrected_series_number
        rows.append(
            DeepCheckRow(
                book_id=d.book_id,
                verdict=result.verdict,
                explanation=result.explanation,
                suggested_title=d.deep_check_suggested_title,
                suggested_author=d.deep_check_suggested_author,
                suggested_series=d.deep_check_suggested_series,
                suggested_series_number=d.deep_check_suggested_series_number,
            )
        )

    await asyncio.gather(*(one(d) for d in targets))
    await save_report(session, report)

    return DeepCheckResult(
        rechecked=len(rows),
        stored_is_wrong=sum(1 for r in rows if r.verdict == "stored_is_wrong"),
        stored_is_correct=sum(1 for r in rows if r.verdict == "stored_is_correct"),
        uncertain=sum(1 for r in rows if r.verdict == "uncertain"),
        failed=failed,
        rows=rows,
    )


# --------------------------------------------------------------------------
# Rebuild job (tracked, in-memory — same pattern as CoverService)
# --------------------------------------------------------------------------


class ReidentAuditService:
    def __init__(self) -> None:
        self._jobs: dict[str, ReidentRebuildJobStatus] = {}

    def create_job(self) -> ReidentRebuildJobStatus:
        job_id = str(uuid.uuid4())
        status = ReidentRebuildJobStatus(job_id=job_id, status=ReidentRebuildJobState.running)
        self._jobs[job_id] = status
        return status

    def get_status(self, job_id: str) -> ReidentRebuildJobStatus | None:
        return self._jobs.get(job_id)

    def has_running_job(self) -> bool:
        return any(j.status == ReidentRebuildJobState.running for j in self._jobs.values())

    async def run(self, job_id: str) -> None:
        def progress(checked: int, total: int, flagged: int) -> None:
            self._jobs[job_id] = ReidentRebuildJobStatus(
                job_id=job_id,
                status=ReidentRebuildJobState.running,
                checked=checked,
                total=total,
                flagged=flagged,
            )

        try:
            report = await build_reident_report(on_progress=progress)
            async with async_session_factory() as session:
                await save_report(session, report)
        except Exception as exc:
            logger.exception("reident: rebuild job failed")
            self._jobs[job_id] = ReidentRebuildJobStatus(
                job_id=job_id, status=ReidentRebuildJobState.failed, detail=str(exc)
            )
            return

        self._jobs[job_id] = ReidentRebuildJobStatus(
            job_id=job_id,
            status=ReidentRebuildJobState.done,
            checked=report.checked,
            total=report.total_organised_books,
            flagged=len(report.divergences),
            detail=(
                f"{len(report.divergences)} divergence(s) across "
                f"{report.checked} organised book(s)"
                + (
                    f"; {report.providers_unavailable} book(s) had no provider data"
                    if report.providers_unavailable
                    else ""
                )
            ),
        )


_reident_audit_service = ReidentAuditService()


def get_reident_audit_service() -> ReidentAuditService:
    return _reident_audit_service
