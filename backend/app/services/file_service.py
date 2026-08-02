from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.data.models import AIDecision, Book, File, FileStatus
from app.schemas.files import FileSummary


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
