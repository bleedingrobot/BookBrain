from app.data.models import AIDecision, Author, Book, File, FileStatus, FileStatusReason, Series
from app.services import file_service


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
