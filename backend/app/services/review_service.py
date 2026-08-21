from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.data.models import (
    BookCandidate,
    File,
    FileStatus,
    FileStatusReason,
    Identifier,
    IdentifierType,
    LibraryRule,
    MetadataSource,
    Review,
    ReviewStatus,
    RuleType,
)
from app.providers.drive.provider import DriveProvider
from app.schemas.reviews import CandidateItem, CorrectReviewRequest, EvidenceItem, ReviewDetail, ReviewSummary
from app.services.book_repository import get_book_write_lock, resolve_book
from app.services.text_match import normalize

# Structural issues (a Drive-side conflict, not an identification problem) are
# never cleared by resolving a review — only the human fixing Drive itself
# clears them. low_confidence is the opposite: it *is* the reason the file
# was queued for review, so approving/correcting always clears it.
_STRUCTURAL_REASONS = {
    FileStatusReason.multi_parent,
    FileStatusReason.no_parent,
    FileStatusReason.manual_drift,
}


class ReviewNotFoundError(Exception):
    pass


class ReviewAlreadyResolvedError(Exception):
    pass


async def list_reviews(
    session: AsyncSession, status: ReviewStatus = ReviewStatus.pending
) -> list[ReviewSummary]:
    result = await session.execute(
        select(Review).where(Review.status == status).options(selectinload(Review.file))
    )
    return [_to_summary(review) for review in result.scalars().all()]


async def get_review_detail(session: AsyncSession, review_id: int) -> ReviewDetail:
    review = await _fetch_review(session, review_id)

    evidence_rows = (
        (await session.execute(select(MetadataSource).where(MetadataSource.file_id == review.file_id)))
        .scalars()
        .all()
    )
    candidate_rows = (
        (await session.execute(select(BookCandidate).where(BookCandidate.file_id == review.file_id)))
        .scalars()
        .all()
    )

    summary = _to_summary(review)
    return ReviewDetail(
        **summary.model_dump(),
        proposed_json=review.proposed_json,
        correction_json=review.correction_json,
        reasoning_summary=review.proposed_json.get("reasoning_summary"),
        evidence=[
            EvidenceItem(field_name=e.field_name, value=e.value, source=e.source) for e in evidence_rows
        ],
        candidates=[
            CandidateItem(
                title=c.title,
                author=c.author,
                series=c.series,
                series_number=c.series_number,
                source=c.source,
            )
            for c in candidate_rows
        ],
    )


async def approve(session: AsyncSession, review_id: int) -> Review:
    review = await _fetch_pending_review(session, review_id)
    review.status = ReviewStatus.approved
    review.resolved_at = datetime.now(UTC)

    if review.file.status_reason not in _STRUCTURAL_REASONS:
        review.file.status = FileStatus.inbox
        review.file.status_reason = None

    await session.commit()
    return review


async def reject(session: AsyncSession, review_id: int, provider: DriveProvider) -> Review:
    """Reject removes the file from the book dump entirely (Drive's Trash —
    recoverable, not a permanent delete) rather than leaving a rejected
    identification sitting in the folder to be reconsidered. The File row
    itself is kept (not deleted) specifically so its sha256 stays around:
    if this exact content gets uploaded again later, scan_service's
    duplicate check recognizes it as a re-upload of already-rejected
    content and flags it for cleanup instead of running the full pipeline
    on it again. A structural issue (multi-parent, etc.) becomes moot once
    the file is gone, so — unlike approve/correct — reject always clears
    it rather than leaving it blocking anything."""
    review = await _fetch_pending_review(session, review_id)

    provider.trash_file(review.file.drive_file_id)

    review.status = ReviewStatus.rejected
    review.resolved_at = datetime.now(UTC)

    review.file.book_id = None
    review.file.status = FileStatus.rejected
    review.file.status_reason = None

    await session.commit()
    return review


async def correct(session: AsyncSession, review_id: int, body: CorrectReviewRequest) -> Review:
    review = await _fetch_pending_review(session, review_id)
    file_row = review.file

    review.status = ReviewStatus.corrected
    review.correction_json = {
        "title": body.title,
        "author": body.author,
        "series": body.series,
        "series_number": body.series_number,
    }
    review.resolved_at = datetime.now(UTC)

    isbn13, isbn10 = await _identifiers_for_book(session, file_row.book_id)
    # Shared with scan_service's per-file pipeline: resolve_book's fuzzy
    # Author/Series/Book find-or-create must serialize against a concurrent
    # scan doing the same, or a human correction and an in-flight scan can
    # each decide the same not-yet-seen author doesn't exist yet and both
    # create it.
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
    file_row.book_id = book.id
    if file_row.status_reason not in _STRUCTURAL_REASONS:
        file_row.status = FileStatus.inbox
        file_row.status_reason = None

    if body.apply_to_similar:
        await _create_alias_rules(session, review, body)

    await session.commit()
    return review


async def _create_alias_rules(session: AsyncSession, review: Review, body: CorrectReviewRequest) -> None:
    proposed_author = review.proposed_json.get("author")
    if body.author and proposed_author and normalize(body.author) != normalize(proposed_author):
        session.add(
            LibraryRule(
                rule_type=RuleType.author_alias,
                pattern=proposed_author,
                resolution_json={"author": body.author},
                created_from_review_id=review.id,
            )
        )

    proposed_series = review.proposed_json.get("series")
    if body.series and proposed_series and normalize(body.series) != normalize(proposed_series):
        session.add(
            LibraryRule(
                rule_type=RuleType.series_alias,
                pattern=proposed_series,
                resolution_json={"series": body.series},
                created_from_review_id=review.id,
            )
        )


async def _identifiers_for_book(
    session: AsyncSession, book_id: int | None
) -> tuple[str | None, str | None]:
    if book_id is None:
        return None, None
    rows = (
        (await session.execute(select(Identifier).where(Identifier.book_id == book_id))).scalars().all()
    )
    isbn13 = next((r.value for r in rows if r.type == IdentifierType.isbn13), None)
    isbn10 = next((r.value for r in rows if r.type == IdentifierType.isbn10), None)
    return isbn13, isbn10


async def _fetch_review(session: AsyncSession, review_id: int) -> Review:
    result = await session.execute(
        select(Review).where(Review.id == review_id).options(selectinload(Review.file))
    )
    review = result.scalar_one_or_none()
    if review is None:
        raise ReviewNotFoundError(f"review {review_id} not found")
    return review


async def _fetch_pending_review(session: AsyncSession, review_id: int) -> Review:
    review = await _fetch_review(session, review_id)
    if review.status != ReviewStatus.pending:
        raise ReviewAlreadyResolvedError(f"review {review_id} is already {review.status.value}")
    return review


def _to_summary(review: Review) -> ReviewSummary:
    file_row: File = review.file
    proposed = review.proposed_json or {}
    return ReviewSummary(
        id=review.id,
        file_id=file_row.id,
        filename=file_row.filename,
        status=review.status.value,
        status_reason=file_row.status_reason.value if file_row.status_reason else None,
        proposed_title=proposed.get("title"),
        proposed_author=proposed.get("author"),
        computed_confidence=proposed.get("computed_confidence"),
    )
