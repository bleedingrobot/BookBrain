from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.data.models import (
    AIDecision,
    Author,
    Book,
    File,
    FileStatus,
    FileStatusReason,
    Identifier,
    IdentifierType,
    Review,
    ReviewStatus,
    Series,
)
from app.providers.drive.provider import DriveProvider
from app.schemas.files import FileSummary
from app.schemas.reviews import CorrectReviewRequest
from app.services.book_repository import get_book_write_lock, resolve_book, resolve_series

# A Drive-side problem (multi-parent, etc.) isn't cleared by fixing the
# book's metadata — mirrors review_service._STRUCTURAL_REASONS.
_STRUCTURAL_REASONS = {
    FileStatusReason.multi_parent,
    FileStatusReason.no_parent,
    FileStatusReason.manual_drift,
}


class FileRecordNotFoundError(Exception):
    pass


class FileNotIdentifiedError(Exception):
    pass


async def list_files(session: AsyncSession, status: FileStatus | None = None) -> list[FileSummary]:
    """Every file the app knows about, whatever its status — so nothing a
    scan found is ever invisible without deliberately looking for it."""
    query = select(File).options(
        selectinload(File.book).selectinload(Book.author),
        selectinload(File.book).selectinload(Book.series),
        selectinload(File.ai_decisions),
    )
    if status is not None:
        query = query.where(File.status == status)
    query = query.order_by(File.discovered_at.desc())

    files = (await session.execute(query)).scalars().all()
    return [_to_summary(f) for f in files]


async def remove_file(session: AsyncSession, file_id: int, provider: DriveProvider) -> File:
    """For files with no other way to clear them out — most notably
    `unidentified` (a parse failure never gets a Review row, so
    review_service.reject's flow doesn't apply). Trashes the Drive file
    (recoverable via Drive's own Trash, not a permanent delete) and marks
    the row `rejected`, the same terminal state review-reject leaves
    behind — kept, not deleted, so its sha256 is still recognized if this
    exact content is ever re-uploaded, instead of re-running the full
    pipeline (and hitting the same parse failure) on it again."""
    file_row = await session.get(File, file_id)
    if file_row is None:
        raise FileRecordNotFoundError(f"file {file_id} not found")

    provider.trash_file(file_row.drive_file_id)
    file_row.book_id = None
    file_row.status = FileStatus.rejected
    file_row.status_reason = None
    await session.commit()
    return file_row


async def correct_file(session: AsyncSession, file_id: int, body: CorrectReviewRequest) -> File:
    """Fix an identified file's metadata from the Library page — for books
    that were auto-organized and so never had a Review row for
    review_service.correct() to act on. Records a sticky `corrected` review
    (keyed by sha256, SPEC.md §1), re-points the file at the corrected
    book, invalidates the AI-decision cache for that content, and drops the
    file back to `inbox` so the next Organize re-shelves it."""
    file_row = await session.get(File, file_id)
    if file_row is None:
        raise FileRecordNotFoundError(f"file {file_id} not found")
    if file_row.book_id is None:
        raise FileNotIdentifiedError(
            "this file hasn't been identified yet — resolve it in the review queue instead"
        )

    old = (
        await session.execute(
            select(Book)
            .where(Book.id == file_row.book_id)
            .options(selectinload(Book.author), selectinload(Book.series))
        )
    ).scalar_one_or_none()
    session.add(
        Review(
            file_id=file_id,
            status=ReviewStatus.corrected,
            proposed_json={
                "title": old.canonical_title if old else None,
                "author": old.author.name if old and old.author else None,
                "series": old.series.name if old and old.series else None,
                "series_number": old.series_number if old else None,
            },
            correction_json={
                "title": body.title,
                "author": body.author,
                "series": body.series,
                "series_number": body.series_number,
            },
            resolved_at=datetime.now(UTC),
        )
    )

    isbn13, isbn10 = await _isbns_for_book(session, file_row.book_id)
    async with get_book_write_lock():
        book = await resolve_book(
            session,
            title=body.title,
            author=body.author,
            series=body.series,
            series_number=body.series_number,
            isbn13=isbn13,
            isbn10=isbn10,
        )
        # resolve_book only sets series on rows it creates — for an existing
        # match the correction is authoritative, so set it here.
        series_row = await resolve_series(session, body.series)
        book.series_id = series_row.id if series_row else None
        book.series_number = body.series_number

    file_row.book_id = book.id
    await session.execute(delete(AIDecision).where(AIDecision.file_id == file_id))

    if file_row.status_reason not in _STRUCTURAL_REASONS:
        file_row.status = FileStatus.inbox
        file_row.status_reason = None

    await session.commit()
    return file_row


async def _isbns_for_book(
    session: AsyncSession, book_id: int
) -> tuple[str | None, str | None]:
    rows = (
        (await session.execute(select(Identifier).where(Identifier.book_id == book_id)))
        .scalars()
        .all()
    )
    isbn13 = next((r.value for r in rows if r.type == IdentifierType.isbn13), None)
    isbn10 = next((r.value for r in rows if r.type == IdentifierType.isbn10), None)
    return isbn13, isbn10


def _to_summary(file_row: File) -> FileSummary:
    book = file_row.book
    decision = _latest_decision(file_row.ai_decisions)
    return FileSummary(
        id=file_row.id,
        filename=file_row.filename,
        status=file_row.status.value,
        status_reason=file_row.status_reason.value if file_row.status_reason else None,
        book_title=book.canonical_title if book else None,
        book_author=book.author.name if book and book.author else None,
        book_series=book.series.name if book and book.series else None,
        book_series_number=book.series_number if book else None,
        computed_confidence=decision.computed_confidence if decision else None,
        ai_reasoning=decision.reasoning_summary if decision else None,
        quality_score=file_row.quality_score,
        discovered_at=file_row.discovered_at.isoformat(),
    )


def _latest_decision(decisions: list[AIDecision]) -> AIDecision | None:
    if not decisions:
        return None
    # id, not created_at: SQLite's default timestamp resolution can tie
    # two decisions created in the same test/request, and id is guaranteed
    # to reflect insertion order where created_at might not.
    return max(decisions, key=lambda d: d.id)
