import pytest
from sqlalchemy import select

from app.data.models import (
    AIDecision,
    Author,
    Book,
    File,
    FileStatus,
    FileStatusReason,
    Review,
    ReviewStatus,
    Series,
)
from app.schemas.reviews import CorrectReviewRequest
from app.services import file_service


class _FakeProvider:
    def __init__(self, fail_for: set[str] | None = None) -> None:
        self.trashed: list[str] = []
        self._fail_for = fail_for or set()

    def trash_file(self, file_id: str) -> dict:
        if file_id in self._fail_for:
            raise RuntimeError("simulated Drive failure")
        self.trashed.append(file_id)
        return {"id": file_id, "trashed": True}


async def _seed_file(
    db_session,
    *,
    drive_file_id: str,
    filename: str,
    status: FileStatus,
    status_reason: FileStatusReason | None = None,
    book_id: int | None = None,
) -> File:
    file_row = File(
        drive_file_id=drive_file_id,
        drive_parent_id="p",
        filename=filename,
        sha256=f"sha-{drive_file_id}",
        size_bytes=100,
        status=status,
        status_reason=status_reason,
        book_id=book_id,
    )
    db_session.add(file_row)
    await db_session.commit()
    await db_session.refresh(file_row)
    return file_row


async def test_list_files_includes_every_status_by_default(db_session) -> None:
    await _seed_file(db_session, drive_file_id="1", filename="a.epub", status=FileStatus.inbox)
    await _seed_file(
        db_session,
        drive_file_id="2",
        filename="b.epub",
        status=FileStatus.unidentified,
        status_reason=FileStatusReason.low_confidence,
    )

    summaries = await file_service.list_files(db_session)

    assert {s.filename for s in summaries} == {"a.epub", "b.epub"}


async def test_list_files_filters_by_status(db_session) -> None:
    await _seed_file(db_session, drive_file_id="1", filename="a.epub", status=FileStatus.inbox)
    await _seed_file(
        db_session,
        drive_file_id="2",
        filename="b.epub",
        status=FileStatus.unidentified,
        status_reason=FileStatusReason.low_confidence,
    )

    summaries = await file_service.list_files(db_session, FileStatus.unidentified)

    assert len(summaries) == 1
    assert summaries[0].filename == "b.epub"
    assert summaries[0].status_reason == "low_confidence"


async def test_list_files_includes_book_and_confidence_when_present(db_session) -> None:
    author = Author(name="Frank Herbert")
    db_session.add(author)
    await db_session.flush()
    series = Series(name="Dune Chronicles")
    db_session.add(series)
    await db_session.flush()
    book = Book(
        canonical_title="Dune", author_id=author.id, series_id=series.id, series_number=1
    )
    db_session.add(book)
    await db_session.flush()

    file_row = await _seed_file(
        db_session,
        drive_file_id="1",
        filename="dune.epub",
        status=FileStatus.review,
        status_reason=FileStatusReason.low_confidence,
        book_id=book.id,
    )
    db_session.add(
        AIDecision(
            file_id=file_row.id,
            model="claude-opus-5",
            prompt_hash="h",
            evidence_hash="e",
            raw_response_json={},
            computed_confidence=45,
            reasoning_summary="Thin evidence but plausible.",
        )
    )
    await db_session.commit()

    summaries = await file_service.list_files(db_session)

    assert len(summaries) == 1
    summary = summaries[0]
    assert summary.book_title == "Dune"
    assert summary.book_author == "Frank Herbert"
    assert summary.book_series == "Dune Chronicles"
    assert summary.book_series_number == 1
    assert summary.computed_confidence == 45
    assert summary.ai_reasoning == "Thin evidence but plausible."


async def test_list_files_uses_latest_ai_decision(db_session) -> None:
    file_row = await _seed_file(
        db_session, drive_file_id="1", filename="a.epub", status=FileStatus.review
    )
    db_session.add(
        AIDecision(
            file_id=file_row.id,
            model="claude-opus-5",
            prompt_hash="h1",
            evidence_hash="e1",
            raw_response_json={},
            computed_confidence=40,
            reasoning_summary="first pass",
        )
    )
    await db_session.commit()
    db_session.add(
        AIDecision(
            file_id=file_row.id,
            model="claude-opus-5",
            prompt_hash="h2",
            evidence_hash="e2",
            raw_response_json={},
            computed_confidence=80,
            reasoning_summary="second pass",
        )
    )
    await db_session.commit()

    summaries = await file_service.list_files(db_session)

    assert summaries[0].computed_confidence == 80
    assert summaries[0].ai_reasoning == "second pass"


async def test_list_files_handles_no_book_or_decision(db_session) -> None:
    await _seed_file(db_session, drive_file_id="1", filename="a.epub", status=FileStatus.inbox)

    summaries = await file_service.list_files(db_session)

    assert summaries[0].book_title is None
    assert summaries[0].book_author is None
    assert summaries[0].book_series is None
    assert summaries[0].computed_confidence is None
    assert summaries[0].ai_reasoning is None


async def test_remove_file_trashes_drive_file_and_marks_rejected(db_session) -> None:
    file_row = await _seed_file(
        db_session,
        drive_file_id="drive-1",
        filename="broken.epub",
        status=FileStatus.unidentified,
        status_reason=FileStatusReason.parse_failed,
    )
    provider = _FakeProvider()

    result = await file_service.remove_file(db_session, file_row.id, provider)

    assert provider.trashed == ["drive-1"]
    assert result.status == FileStatus.rejected
    assert result.status_reason is None
    assert result.book_id is None


async def test_remove_file_clears_book_id(db_session) -> None:
    author = Author(name="Some Author")
    db_session.add(author)
    await db_session.flush()
    book = Book(canonical_title="Some Book", author_id=author.id)
    db_session.add(book)
    await db_session.flush()
    file_row = await _seed_file(
        db_session, drive_file_id="drive-1", filename="a.epub", status=FileStatus.review, book_id=book.id
    )
    provider = _FakeProvider()

    result = await file_service.remove_file(db_session, file_row.id, provider)

    assert result.book_id is None


async def test_remove_file_raises_for_unknown_id(db_session) -> None:
    provider = _FakeProvider()

    with pytest.raises(file_service.FileRecordNotFoundError):
        await file_service.remove_file(db_session, 9999, provider)

    assert provider.trashed == []


async def test_remove_file_propagates_drive_failure_without_committing(db_session) -> None:
    file_row = await _seed_file(
        db_session, drive_file_id="drive-1", filename="a.epub", status=FileStatus.unidentified
    )
    provider = _FakeProvider(fail_for={"drive-1"})

    with pytest.raises(RuntimeError):
        await file_service.remove_file(db_session, file_row.id, provider)

    await db_session.refresh(file_row)
    assert file_row.status == FileStatus.unidentified


async def _organised_book_file(db_session, *, title, author_name, series_name=None, series_number=None):
    author = Author(name=author_name)
    db_session.add(author)
    await db_session.flush()
    series = None
    if series_name:
        series = Series(name=series_name)
        db_session.add(series)
        await db_session.flush()
    book = Book(
        canonical_title=title,
        author_id=author.id,
        series_id=series.id if series else None,
        series_number=series_number,
    )
    db_session.add(book)
    await db_session.flush()
    file_row = await _seed_file(
        db_session,
        drive_file_id=f"d-{title}",
        filename=f"{title}.epub",
        status=FileStatus.organised,
        book_id=book.id,
    )
    return file_row, book


async def test_correct_file_re_files_and_records_a_sticky_review(db_session) -> None:
    file_row, wrong_book = await _organised_book_file(
        db_session,
        title="Scion",
        author_name="James Islington",
        series_name="The Hierarchy",
        series_number=2,
    )
    db_session.add(
        AIDecision(
            file_id=file_row.id,
            model="m",
            prompt_hash="p",
            evidence_hash="e",
            raw_response_json={},
            computed_confidence=85,
        )
    )
    await db_session.commit()

    await file_service.correct_file(
        db_session,
        file_row.id,
        CorrectReviewRequest(title="Scion", author="James Islington", series=None, series_number=None),
    )
    await db_session.refresh(file_row)
    await db_session.refresh(wrong_book)

    assert file_row.status == FileStatus.inbox
    assert wrong_book.series_id is None and wrong_book.series_number is None

    reviews = (await db_session.execute(select(Review))).scalars().all()
    assert len(reviews) == 1 and reviews[0].status == ReviewStatus.corrected
    assert reviews[0].correction_json["series"] is None
    assert reviews[0].proposed_json["series"] == "The Hierarchy"

    left = (
        await db_session.execute(select(AIDecision).where(AIDecision.file_id == file_row.id))
    ).scalars().all()
    assert left == []


async def test_confirm_files_batch_is_idempotent_and_skips_bad_ids(db_session) -> None:
    f1, _ = await _organised_book_file(db_session, title="Book One", author_name="A")
    f2, _ = await _organised_book_file(db_session, title="Book Two", author_name="B")
    unidentified = await _seed_file(
        db_session, drive_file_id="x", filename="x.epub", status=FileStatus.inbox
    )

    confirmed, skipped = await file_service.confirm_files(
        db_session, [f1.id, f2.id, unidentified.id, 9999]
    )
    assert confirmed == 2
    assert skipped == 2

    # Re-running is a no-op (still "confirmed", no duplicate Review rows).
    confirmed, _ = await file_service.confirm_files(db_session, [f1.id, f2.id])
    assert confirmed == 2
    reviews = (await db_session.execute(select(Review))).scalars().all()
    assert len(reviews) == 2
    assert all(r.status == ReviewStatus.approved for r in reviews)


async def test_correct_file_rejects_an_unidentified_file(db_session) -> None:
    file_row = await _seed_file(
        db_session, drive_file_id="u1", filename="u.epub", status=FileStatus.unidentified
    )
    with pytest.raises(file_service.FileNotIdentifiedError):
        await file_service.correct_file(db_session, file_row.id, CorrectReviewRequest(title="X"))
