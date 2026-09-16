"""prompts/15 Stage I — the "Recently auto-organized" tray.

Everything that clears `confidence_auto_flagged` auto-organizes with no human
eyes on it. This is the thin safety net: a read-only listing of what was moved
in the last window (plus, when the soft-hold is on, what is *about* to be
moved), so a human glance within a day catches the rare miss. No AI calls — all
DB reads.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.data.models import (
    AIDecision,
    Book,
    File,
    FileStatus,
    Operation,
    OperationAction,
    OperationStatus,
    Review,
    ReviewStatus,
)
from app.data.repositories.settings_repository import SettingsRepository
from app.schemas.recently_organized import (
    HeldFileItem,
    RecentlyOrganizedItem,
    RecentlyOrganizedResponse,
)
from app.services.organize_service import get_organize_hold_hours

# `since=` accepts a `24h` / `48h` / `7d` style string. Clamp to a month — the
# tray is a same-day-glance tool, not an archive.
_MAX_SINCE_HOURS = 30 * 24
_DEFAULT_SINCE_HOURS = 48

_ORGANIZE_ACTIONS = (
    OperationAction.move,
    OperationAction.rename,
    OperationAction.move_and_rename,
)
_PROVIDER_SOURCES = {"google_books", "open_library"}
# The marker file_service.confirm_file stamps on its Review(status=approved) row.
CONFIRM_MARKER = "confirmed"


def parse_since(value: str | None) -> int:
    """`"24h"` / `"48h"` / `"7d"` / a bare number of hours → an int hour count,
    clamped to (0, _MAX_SINCE_HOURS]. Anything unparseable falls back to the
    48h default rather than erroring — this drives a dashboard panel, not a
    destructive action."""
    if not value:
        return _DEFAULT_SINCE_HOURS
    text = value.strip().lower()
    try:
        if text.endswith("h"):
            hours = int(text[:-1])
        elif text.endswith("d"):
            hours = int(text[:-1]) * 24
        else:
            hours = int(text)
    except ValueError:
        return _DEFAULT_SINCE_HOURS
    if hours <= 0:
        return _DEFAULT_SINCE_HOURS
    return min(hours, _MAX_SINCE_HOURS)


def _latest_decision(file_row: File) -> AIDecision | None:
    if not file_row.ai_decisions:
        return None
    return max(file_row.ai_decisions, key=lambda d: d.id)


def _evidence_summary(file_row: File, decision: AIDecision | None) -> str:
    """A short, human "what the identification was based on" line — the AI
    reasoning plus which corroborating signals were present."""
    parts: list[str] = []

    if decision is not None and decision.model == "deterministic":
        parts.append("Deterministic: ISBN + provider + EPUB metadata agree")
    elif decision is not None and decision.reasoning_summary:
        parts.append(decision.reasoning_summary.strip())

    has_isbn = any(
        ident.type.value in ("isbn10", "isbn13")
        for ident in (file_row.book.identifiers if file_row.book else [])
    )
    if has_isbn:
        parts.append("ISBN in file")

    provider_hits = sum(
        1 for c in file_row.candidates if c.source in _PROVIDER_SOURCES
    )
    if provider_hits:
        parts.append(f"{provider_hits} provider match{'es' if provider_hits != 1 else ''}")

    raw = (decision.raw_response_json if decision else None) or {}
    if raw.get("grounding"):
        parts.append("web-search verified")
    if raw.get("batch_prior"):
        parts.append("supported by a batch of sibling files")
    verification = raw.get("verification") or {}
    if verification.get("verdict") == "stored_is_correct":
        parts.append("double-checked by a second AI pass")

    if not parts:
        return "No identification details recorded."
    return " · ".join(parts)


async def _confirmed_file_ids(session: AsyncSession, file_ids: list[int]) -> set[int]:
    if not file_ids:
        return set()
    rows = (
        (
            await session.execute(
                select(Review).where(
                    Review.file_id.in_(file_ids),
                    Review.status == ReviewStatus.approved,
                )
            )
        )
        .scalars()
        .all()
    )
    return {r.file_id for r in rows if (r.proposed_json or {}).get(CONFIRM_MARKER)}


def _book_fields(book: Book | None) -> tuple[str | None, str | None, str | None, float | None]:
    if book is None:
        return None, None, None, None
    return (
        book.canonical_title,
        book.author.name if book.author else None,
        book.series.name if book.series else None,
        book.series_number,
    )


_FILE_LOADERS = (
    selectinload(File.book).selectinload(Book.author),
    selectinload(File.book).selectinload(Book.series),
    selectinload(File.book).selectinload(Book.identifiers),
    selectinload(File.ai_decisions),
    selectinload(File.candidates),
)


async def recently_organized(
    session: AsyncSession, *, since_hours: int
) -> RecentlyOrganizedResponse:
    hold_hours = await get_organize_hold_hours(SettingsRepository(session))
    now = datetime.now(UTC).replace(tzinfo=None)
    cutoff = now - timedelta(hours=since_hours)

    op_rows = (
        (
            await session.execute(
                select(Operation)
                .where(
                    Operation.action.in_(_ORGANIZE_ACTIONS),
                    Operation.status == OperationStatus.done,
                    Operation.dry_run.is_(False),
                    Operation.timestamp >= cutoff,
                )
                .order_by(Operation.timestamp.desc())
            )
        )
        .scalars()
        .all()
    )

    # One row per file — the newest move in the window (a file organized, then
    # corrected, then re-organized shows once). Files are re-fetched with their
    # relationships in a single follow-up query.
    file_ids_in_order: list[int] = []
    latest_op_by_file: dict[int, Operation] = {}
    for op in op_rows:
        if op.file_id not in latest_op_by_file:  # rows are newest-first
            latest_op_by_file[op.file_id] = op
            file_ids_in_order.append(op.file_id)

    files_by_id: dict[int, File] = {}
    if file_ids_in_order:
        files = (
            (
                await session.execute(
                    select(File).where(File.id.in_(file_ids_in_order)).options(*_FILE_LOADERS)
                )
            )
            .scalars()
            .all()
        )
        files_by_id = {f.id: f for f in files}

    confirmed = await _confirmed_file_ids(session, file_ids_in_order)

    organized: list[RecentlyOrganizedItem] = []
    for file_id in file_ids_in_order:
        file_row = files_by_id.get(file_id)
        if file_row is None:
            continue
        op = latest_op_by_file[file_id]
        decision = _latest_decision(file_row)
        title, author, series, series_number = _book_fields(file_row.book)
        confidence = op.confidence
        if confidence is None and decision is not None:
            confidence = decision.computed_confidence
        organized.append(
            RecentlyOrganizedItem(
                file_id=file_id,
                operation_id=op.id,
                organized_at=op.timestamp.isoformat(),
                filename=file_row.filename,
                title=title,
                author=author,
                series=series,
                series_number=series_number,
                confidence=confidence,
                current_status=file_row.status.value,
                evidence_summary=_evidence_summary(file_row, decision),
                confirmed=file_id in confirmed,
            )
        )

    held: list[HeldFileItem] = []
    if hold_hours > 0:
        hold_cutoff = now - timedelta(hours=hold_hours)
        held_files = (
            (
                await session.execute(
                    select(File)
                    .where(
                        File.status == FileStatus.inbox,
                        File.book_id.is_not(None),
                        File.discovered_at > hold_cutoff,
                    )
                    .order_by(File.discovered_at.desc())
                    .options(*_FILE_LOADERS)
                )
            )
            .scalars()
            .all()
        )
        for file_row in held_files:
            decision = _latest_decision(file_row)
            title, author, series, series_number = _book_fields(file_row.book)
            held.append(
                HeldFileItem(
                    file_id=file_row.id,
                    filename=file_row.filename,
                    title=title,
                    author=author,
                    series=series,
                    series_number=series_number,
                    confidence=decision.computed_confidence if decision else None,
                    evidence_summary=_evidence_summary(file_row, decision),
                    held_since=file_row.discovered_at.isoformat(),
                    eligible_at=(
                        file_row.discovered_at + timedelta(hours=hold_hours)
                    ).isoformat(),
                )
            )

    return RecentlyOrganizedResponse(
        since_hours=since_hours,
        hold_hours=hold_hours,
        organized=organized,
        held=held,
    )
