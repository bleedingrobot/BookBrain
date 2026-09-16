from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.data.models import (
    Author,
    Book,
    File,
    FileStatus,
    FileStatusReason,
    Identifier,
    IdentifierType,
    LibraryRule,
    MetadataSource,
    Review,
    ReviewStatus,
)
from app.schemas.reviews import CorrectReviewRequest
from app.services import review_service
from app.services.review_service import ReviewAlreadyResolvedError, ReviewNotFoundError


class _FakeProvider:
    def __init__(self) -> None:
        self.trashed: list[str] = []

    def trash_file(self, file_id: str) -> dict:
        self.trashed.append(file_id)
        return {"id": file_id, "trashed": True}


async def _seed_review(
    db_session,
    *,
    status_reason: FileStatusReason | None = FileStatusReason.low_confidence,
    proposed_author: str = "Original Author",
    proposed_series: str | None = None,
    book_id: int | None = None,
) -> Review:
    file_row = File(
        drive_file_id="drive-1",
        drive_parent_id="p",
        filename="dune.epub",
        sha256="abc123",
        size_bytes=100,
        status=FileStatus.review,
        status_reason=status_reason,
        book_id=book_id,
    )
    db_session.add(file_row)
    await db_session.flush()

    db_session.add(MetadataSource(file_id=file_row.id, field_name="title", value="Dune", source="epub"))

    review = Review(
        file_id=file_row.id,
        status=ReviewStatus.pending,
        proposed_json={
            "title": "Dune",
            "author": proposed_author,
            "series": proposed_series,
            "series_number": None,
            "computed_confidence": 75,
            "ai_reported_confidence": None,
            "reasoning_summary": "test",
        },
    )
    db_session.add(review)
    await db_session.commit()
    return review


async def test_list_reviews_returns_pending_by_default(db_session) -> None:
    await _seed_review(db_session)

    reviews = await review_service.list_reviews(db_session)

    assert len(reviews) == 1
    assert reviews[0].status == "pending"
    assert reviews[0].proposed_title == "Dune"
    assert reviews[0].computed_confidence == 75


async def test_get_review_detail_includes_evidence(db_session) -> None:
    review = await _seed_review(db_session)

    detail = await review_service.get_review_detail(db_session, review.id)

    assert detail.evidence[0].field_name == "title"
    assert detail.evidence[0].value == "Dune"
    assert detail.candidates == []


async def test_get_review_detail_raises_for_missing_id(db_session) -> None:
    with pytest.raises(ReviewNotFoundError):
        await review_service.get_review_detail(db_session, 999)


async def test_approve_promotes_low_confidence_file_to_inbox(db_session) -> None:
    review = await _seed_review(db_session, status_reason=FileStatusReason.low_confidence)

    await review_service.approve(db_session, review.id)

    file_row = (await db_session.execute(select(File))).scalar_one()
    assert file_row.status.value == "inbox"
    assert file_row.status_reason is None

    updated = (await db_session.execute(select(Review))).scalar_one()
    assert updated.status.value == "approved"
    assert updated.resolved_at is not None


async def test_approve_does_not_clear_structural_status_reason(db_session) -> None:
    review = await _seed_review(db_session, status_reason=FileStatusReason.multi_parent)

    await review_service.approve(db_session, review.id)

    file_row = (await db_session.execute(select(File))).scalar_one()
    assert file_row.status.value == "review"
    assert file_row.status_reason.value == "multi_parent"


async def test_approve_twice_raises(db_session) -> None:
    review = await _seed_review(db_session)
    await review_service.approve(db_session, review.id)

    with pytest.raises(ReviewAlreadyResolvedError):
        await review_service.approve(db_session, review.id)


async def test_reject_trashes_drive_file_clears_book_and_marks_rejected(db_session) -> None:
    author = Author(name="X")
    db_session.add(author)
    await db_session.flush()
    book = Book(canonical_title="Wrong Book", author_id=author.id)
    db_session.add(book)
    await db_session.flush()

    review = await _seed_review(db_session, book_id=book.id)
    provider = _FakeProvider()

    await review_service.reject(db_session, review.id, provider)

    assert provider.trashed == ["drive-1"]

    file_row = (await db_session.execute(select(File))).scalar_one()
    assert file_row.book_id is None
    assert file_row.status.value == "rejected"
    assert file_row.status_reason is None

    updated = (await db_session.execute(select(Review))).scalar_one()
    assert updated.status.value == "rejected"
    assert updated.resolved_at is not None


async def test_reject_clears_structural_reason_too(db_session) -> None:
    # Unlike approve/correct, reject removes the file from Drive entirely —
    # an unresolved structural issue (multi-parent, etc.) becomes moot once
    # there's nothing left to fix.
    review = await _seed_review(db_session, status_reason=FileStatusReason.multi_parent)
    provider = _FakeProvider()

    await review_service.reject(db_session, review.id, provider)

    file_row = (await db_session.execute(select(File))).scalar_one()
    assert file_row.status.value == "rejected"
    assert file_row.status_reason is None


async def test_reject_leaves_review_pending_when_drive_trash_fails(db_session) -> None:
    class _FailingProvider:
        def trash_file(self, file_id: str) -> dict:
            raise RuntimeError("simulated Drive failure")

    review = await _seed_review(db_session)

    with pytest.raises(RuntimeError):
        await review_service.reject(db_session, review.id, _FailingProvider())

    updated = (await db_session.execute(select(Review))).scalar_one()
    assert updated.status.value == "pending"


async def test_correct_resolves_new_book_and_promotes_status(db_session) -> None:
    review = await _seed_review(db_session, status_reason=FileStatusReason.low_confidence)
    body = CorrectReviewRequest(title="Corrected Title", author="Corrected Author")

    await review_service.correct(db_session, review.id, body)

    file_row = (await db_session.execute(select(File))).scalar_one()
    book = (await db_session.execute(select(Book).where(Book.id == file_row.book_id))).scalar_one()
    assert book.canonical_title == "Corrected Title"
    assert file_row.status.value == "inbox"

    updated = (await db_session.execute(select(Review))).scalar_one()
    assert updated.status.value == "corrected"
    assert updated.correction_json["title"] == "Corrected Title"


async def test_correct_does_not_merge_series_prefix_titles(db_session) -> None:
    # Regression (P0): correcting a file to "<Series>: <Book B>" when a
    # "<Series>: <Book A>" by the same author already exists must not resolve
    # both onto one Book row.
    author = Author(name="Brandon Sanderson")
    db_session.add(author)
    await db_session.flush()
    existing = Book(canonical_title="Mistborn: The Final Empire", author_id=author.id)
    db_session.add(existing)
    await db_session.commit()

    review = await _seed_review(db_session, proposed_author="Brandon Sanderson")
    body = CorrectReviewRequest(
        title="Mistborn: The Well of Ascension", author="Brandon Sanderson"
    )

    await review_service.correct(db_session, review.id, body)

    file_row = (await db_session.execute(select(File))).scalar_one()
    assert file_row.book_id != existing.id
    assert len((await db_session.execute(select(Book))).scalars().all()) == 2


async def test_correct_preserves_isbn_from_prior_book(db_session) -> None:
    author = Author(name="Original Author")
    db_session.add(author)
    await db_session.flush()
    old_book = Book(canonical_title="Dune", author_id=author.id)
    db_session.add(old_book)
    await db_session.flush()
    db_session.add(
        Identifier(book_id=old_book.id, type=IdentifierType.isbn13, value="9780441172719")
    )
    await db_session.commit()

    review = await _seed_review(db_session, book_id=old_book.id)
    body = CorrectReviewRequest(title="Dune", author="Frank Herbert")

    await review_service.correct(db_session, review.id, body)

    file_row = (await db_session.execute(select(File))).scalar_one()
    new_book = (await db_session.execute(select(Book).where(Book.id == file_row.book_id))).scalar_one()
    identifiers = (
        (await db_session.execute(select(Identifier).where(Identifier.book_id == new_book.id)))
        .scalars()
        .all()
    )
    assert [i.value for i in identifiers] == ["9780441172719"]


async def test_correct_with_apply_to_similar_creates_author_alias_rule(db_session) -> None:
    review = await _seed_review(db_session, proposed_author="Jane Author")
    body = CorrectReviewRequest(title="Dune", author="Jane A. Author", apply_to_similar=True)

    await review_service.correct(db_session, review.id, body)

    rules = (await db_session.execute(select(LibraryRule))).scalars().all()
    assert len(rules) == 1
    assert rules[0].rule_type.value == "author_alias"
    assert rules[0].pattern == "Jane Author"
    assert rules[0].resolution_json == {"author": "Jane A. Author"}
    assert rules[0].created_from_review_id == review.id


async def test_correct_without_apply_to_similar_creates_no_rule(db_session) -> None:
    review = await _seed_review(db_session, proposed_author="Jane Author")
    body = CorrectReviewRequest(title="Dune", author="Jane A. Author", apply_to_similar=False)

    await review_service.correct(db_session, review.id, body)

    assert (await db_session.execute(select(LibraryRule))).scalars().all() == []


async def test_correct_apply_to_similar_no_rule_when_author_unchanged(db_session) -> None:
    review = await _seed_review(db_session, proposed_author="Jane Author")
    body = CorrectReviewRequest(title="Dune Corrected", author="Jane Author", apply_to_similar=True)

    await review_service.correct(db_session, review.id, body)

    assert (await db_session.execute(select(LibraryRule))).scalars().all() == []


async def _seed_corrected(db_session, *, proposed, corrected, resolved_at, drive_id) -> None:
    file_row = File(
        drive_file_id=drive_id,
        drive_parent_id="p",
        filename=f"{drive_id}.epub",
        sha256=drive_id,
        size_bytes=1,
        status=FileStatus.inbox,
    )
    db_session.add(file_row)
    await db_session.flush()
    db_session.add(
        Review(
            file_id=file_row.id,
            status=ReviewStatus.corrected,
            proposed_json=proposed,
            correction_json=corrected,
            resolved_at=resolved_at,
        )
    )
    await db_session.commit()


def _pair(*, title, author, series, number, to_series=None, to_number=None, to_title=None):
    return dict(
        proposed={"title": title, "author": author, "series": series, "series_number": number},
        corrected={
            "title": to_title or title,
            "author": author,
            "series": to_series,
            "series_number": to_number,
        },
    )


async def test_recent_corrections_ranks_author_match_ahead_of_newer_row(db_session) -> None:
    now = datetime.now(UTC)
    await _seed_corrected(
        db_session,
        **_pair(title="Newer", author="Unrelated Author", series="X", number=1),
        resolved_at=now,
        drive_id="newer",
    )
    await _seed_corrected(
        db_session,
        **_pair(title="Older", author="Target Author", series="Y", number=3),
        resolved_at=now - timedelta(days=5),
        drive_id="older",
    )

    out = await review_service.recent_corrections(db_session, author="Target Author")

    assert [row["proposed"]["title"] for row in out] == ["Older", "Newer"]


async def test_recent_corrections_excludes_noop_rows(db_session) -> None:
    await _seed_corrected(
        db_session,
        proposed={"title": "Same", "author": "A", "series": None, "series_number": None},
        corrected={"title": "Same", "author": "A", "series": None, "series_number": None},
        resolved_at=datetime.now(UTC),
        drive_id="noop",
    )

    assert await review_service.recent_corrections(db_session) == []


async def test_recent_corrections_respects_limit_and_recency_order(db_session) -> None:
    now = datetime.now(UTC)
    for i in range(4):
        await _seed_corrected(
            db_session,
            **_pair(title=f"T{i}", author=f"A{i}", series="S", number=1),
            resolved_at=now - timedelta(hours=i),
            drive_id=f"d{i}",
        )

    out = await review_service.recent_corrections(db_session, limit=2)

    assert [row["proposed"]["title"] for row in out] == ["T0", "T1"]
