from datetime import UTC, datetime, timedelta

from sqlalchemy import select

import pytest

from app.data.models import Author, Book, File, FileStatus, FileStatusReason, MetadataSource, Series
from app.services.duplicate_service import (
    DuplicateNotClearableError,
    clear_duplicates,
    clear_one_duplicate,
    detect_same_book_duplicates,
    list_duplicate_groups,
    unflag_duplicate,
)


class _FakeProvider:
    def __init__(self, fail_for: set[str] | None = None) -> None:
        self.trashed: list[str] = []
        self._fail_for = fail_for or set()

    def trash_file(self, file_id: str) -> dict:
        if file_id in self._fail_for:
            raise RuntimeError("simulated Drive failure")
        self.trashed.append(file_id)
        return {"id": file_id, "trashed": True}


async def test_list_duplicate_groups_empty_when_no_duplicates(db_session) -> None:
    db_session.add(
        File(
            drive_file_id="a",
            drive_parent_id="p",
            filename="a.epub",
            sha256="hash-a",
            size_bytes=100,
            status=FileStatus.inbox,
        )
    )
    await db_session.commit()

    groups = await list_duplicate_groups(db_session)

    assert groups == []


async def test_list_duplicate_groups_pairs_duplicate_with_primary(db_session) -> None:
    db_session.add(
        File(
            drive_file_id="a",
            drive_parent_id="p",
            filename="primary.epub",
            sha256="shared-hash",
            size_bytes=100,
            status=FileStatus.inbox,
            quality_score=80,
        )
    )
    db_session.add(
        File(
            drive_file_id="b",
            drive_parent_id="p",
            filename="copy.epub",
            sha256="shared-hash",
            size_bytes=100,
            status=FileStatus.duplicate,
            quality_score=80,
        )
    )
    await db_session.commit()

    groups = await list_duplicate_groups(db_session)

    assert len(groups) == 1
    assert groups[0].duplicate_filename == "copy.epub"
    assert groups[0].primary_filename == "primary.epub"
    assert groups[0].sha256 == "shared-hash"
    assert groups[0].quality_score == 80


async def test_list_duplicate_groups_handles_missing_primary(db_session) -> None:
    db_session.add(
        File(
            drive_file_id="b",
            drive_parent_id="p",
            filename="orphan.epub",
            sha256="orphan-hash",
            size_bytes=100,
            status=FileStatus.duplicate,
        )
    )
    await db_session.commit()

    groups = await list_duplicate_groups(db_session)

    assert len(groups) == 1
    assert groups[0].primary_file_id is None
    assert groups[0].primary_filename is None


async def test_list_duplicate_groups_surfaces_previously_rejected_reason(db_session) -> None:
    db_session.add(
        File(
            drive_file_id="a",
            drive_parent_id="p",
            filename="rejected.epub",
            sha256="rejected-hash",
            size_bytes=100,
            status=FileStatus.rejected,
        )
    )
    db_session.add(
        File(
            drive_file_id="b",
            drive_parent_id="p",
            filename="reupload.epub",
            sha256="rejected-hash",
            size_bytes=100,
            status=FileStatus.duplicate,
            status_reason=FileStatusReason.previously_rejected,
        )
    )
    await db_session.commit()

    groups = await list_duplicate_groups(db_session)

    assert len(groups) == 1
    assert groups[0].status_reason == "previously_rejected"
    assert groups[0].primary_filename == "rejected.epub"


async def _seed_duplicate_pair(db_session) -> tuple[File, File]:
    primary = File(
        drive_file_id="primary-drive-id",
        drive_parent_id="p",
        filename="primary.epub",
        sha256="shared-hash",
        size_bytes=100,
        status=FileStatus.inbox,
    )
    duplicate = File(
        drive_file_id="dup-drive-id",
        drive_parent_id="p",
        filename="copy.epub",
        sha256="shared-hash",
        size_bytes=100,
        status=FileStatus.duplicate,
    )
    db_session.add_all([primary, duplicate])
    await db_session.commit()
    return primary, duplicate


async def test_clear_duplicates_trashes_drive_file_and_deletes_record(db_session) -> None:
    primary, duplicate = await _seed_duplicate_pair(db_session)
    provider = _FakeProvider()

    result = await clear_duplicates(db_session, provider)

    assert result.cleared == 1
    assert result.failed == 0
    assert provider.trashed == ["dup-drive-id"]

    remaining = (await db_session.execute(select(File))).scalars().all()
    assert [f.id for f in remaining] == [primary.id]  # primary untouched


async def test_clear_duplicates_counts_failures_without_deleting_record(db_session) -> None:
    _primary, duplicate = await _seed_duplicate_pair(db_session)
    provider = _FakeProvider(fail_for={"dup-drive-id"})

    result = await clear_duplicates(db_session, provider)

    assert result.cleared == 0
    assert result.failed == 1

    remaining = (await db_session.execute(select(File).where(File.id == duplicate.id))).scalar_one_or_none()
    assert remaining is not None  # left alone since the Drive trash failed


async def test_clear_duplicates_noop_when_none_exist(db_session) -> None:
    provider = _FakeProvider()

    result = await clear_duplicates(db_session, provider)

    assert result.cleared == 0
    assert result.failed == 0


async def test_detect_same_book_duplicates_flags_lower_quality_copy(db_session) -> None:
    better = File(
        drive_file_id="better",
        drive_parent_id="p",
        filename="better.epub",
        sha256="hash-a",
        size_bytes=100,
        status=FileStatus.organised,
        book_id=1,
        quality_score=100,
    )
    worse = File(
        drive_file_id="worse",
        drive_parent_id="p",
        filename="worse.epub",
        sha256="hash-b",  # different bytes — sha256 dedup wouldn't catch this
        size_bytes=100,
        status=FileStatus.organised,
        book_id=1,
        quality_score=45,
    )
    db_session.add_all([better, worse])
    await db_session.commit()

    flagged = await detect_same_book_duplicates(db_session)
    await db_session.commit()

    assert flagged == 1
    await db_session.refresh(better)
    await db_session.refresh(worse)
    assert better.status == FileStatus.organised
    assert worse.status == FileStatus.duplicate
    assert worse.status_reason == FileStatusReason.same_book


async def test_detect_same_book_duplicates_ties_go_to_oldest(db_session) -> None:
    now = datetime.now(UTC)
    older = File(
        drive_file_id="older",
        drive_parent_id="p",
        filename="older.epub",
        sha256="hash-a",
        size_bytes=100,
        status=FileStatus.organised,
        book_id=1,
        quality_score=85,
        discovered_at=now - timedelta(days=1),
    )
    newer = File(
        drive_file_id="newer",
        drive_parent_id="p",
        filename="newer.epub",
        sha256="hash-b",
        size_bytes=100,
        status=FileStatus.organised,
        book_id=1,
        quality_score=85,  # same score — oldest should win, not the other
        discovered_at=now,
    )
    db_session.add_all([newer, older])  # insertion order deliberately reversed
    await db_session.commit()

    await detect_same_book_duplicates(db_session)
    await db_session.commit()

    await db_session.refresh(older)
    await db_session.refresh(newer)
    assert older.status == FileStatus.organised
    assert newer.status == FileStatus.duplicate


async def test_detect_same_book_duplicates_ignores_files_without_a_book(db_session) -> None:
    db_session.add_all(
        [
            File(
                drive_file_id="a",
                drive_parent_id="p",
                filename="a.epub",
                sha256="hash-a",
                size_bytes=100,
                status=FileStatus.review,
                book_id=None,
            ),
            File(
                drive_file_id="b",
                drive_parent_id="p",
                filename="b.epub",
                sha256="hash-b",
                size_bytes=100,
                status=FileStatus.review,
                book_id=None,
            ),
        ]
    )
    await db_session.commit()

    flagged = await detect_same_book_duplicates(db_session)

    assert flagged == 0


async def test_clear_duplicates_leaves_same_book_rows_untouched(db_session) -> None:
    # A same_book row is a resolved-book match, not a byte match — a bad
    # identification could put a real, different book there, so the bulk
    # trash must skip it. Exact-content (sha256, reason=None) dups still go.
    sha_dup = File(
        drive_file_id="sha-dup",
        drive_parent_id="p",
        filename="sha-dup.epub",
        sha256="shared",
        size_bytes=100,
        status=FileStatus.duplicate,
    )
    same_book = File(
        drive_file_id="same-book",
        drive_parent_id="p",
        filename="same-book.epub",
        sha256="different",
        size_bytes=100,
        status=FileStatus.duplicate,
        status_reason=FileStatusReason.same_book,
    )
    db_session.add_all([sha_dup, same_book])
    await db_session.commit()
    provider = _FakeProvider()

    result = await clear_duplicates(db_session, provider)

    assert result.cleared == 1
    assert provider.trashed == ["sha-dup"]
    remaining = (await db_session.execute(select(File.drive_file_id))).scalars().all()
    assert remaining == ["same-book"]


async def test_clear_one_duplicate_trashes_a_single_same_book_row(db_session) -> None:
    same_book = File(
        drive_file_id="same-book",
        drive_parent_id="p",
        filename="same-book.epub",
        sha256="different",
        size_bytes=100,
        status=FileStatus.duplicate,
        status_reason=FileStatusReason.same_book,
    )
    db_session.add(same_book)
    await db_session.commit()
    provider = _FakeProvider()

    result = await clear_one_duplicate(db_session, provider, same_book.id)

    assert result.cleared == 1
    assert provider.trashed == ["same-book"]
    assert (await db_session.execute(select(File))).scalars().all() == []


async def test_clear_one_duplicate_rejects_a_non_duplicate_file(db_session) -> None:
    keeper = File(
        drive_file_id="keeper",
        drive_parent_id="p",
        filename="keeper.epub",
        sha256="x",
        size_bytes=100,
        status=FileStatus.organised,
    )
    db_session.add(keeper)
    await db_session.commit()

    with pytest.raises(DuplicateNotClearableError):
        await clear_one_duplicate(db_session, _FakeProvider(), keeper.id)


async def test_unflag_duplicate_splits_the_file_onto_its_own_book(db_session) -> None:
    author = Author(name="Brandon Sanderson")
    series = Series(name="Mistborn")
    db_session.add_all([author, series])
    await db_session.flush()
    merged = Book(
        canonical_title="Mistborn: The Final Empire",
        author_id=author.id,
        series_id=series.id,
    )
    db_session.add(merged)
    await db_session.flush()
    flagged = File(
        drive_file_id="flagged",
        drive_parent_id="p",
        filename="well-of-ascension.epub",
        sha256="y",
        size_bytes=100,
        status=FileStatus.duplicate,
        status_reason=FileStatusReason.same_book,
        book_id=merged.id,
    )
    db_session.add(flagged)
    await db_session.flush()
    db_session.add(
        MetadataSource(
            file_id=flagged.id,
            field_name="title",
            value="Mistborn: The Well of Ascension",
            source="epub",
        )
    )
    await db_session.commit()

    await unflag_duplicate(db_session, flagged.id)

    await db_session.refresh(flagged)
    assert flagged.status == FileStatus.inbox
    assert flagged.status_reason is None
    assert flagged.book_id != merged.id
    new_book = (
        await db_session.execute(select(Book).where(Book.id == flagged.book_id))
    ).scalar_one()
    assert new_book.canonical_title == "Mistborn: The Well of Ascension"
    assert new_book.author_id == author.id


async def test_list_duplicate_groups_falls_back_to_book_id_for_same_book_reason(db_session) -> None:
    primary = File(
        drive_file_id="primary",
        drive_parent_id="p",
        filename="primary.epub",
        sha256="hash-a",
        size_bytes=100,
        status=FileStatus.organised,
        book_id=7,
    )
    dup = File(
        drive_file_id="dup",
        drive_parent_id="p",
        filename="dup.epub",
        sha256="hash-b",  # different sha256 — the sha256 lookup can't find primary
        size_bytes=100,
        status=FileStatus.duplicate,
        status_reason=FileStatusReason.same_book,
        book_id=7,
    )
    db_session.add_all([primary, dup])
    await db_session.commit()

    groups = await list_duplicate_groups(db_session)

    assert len(groups) == 1
    assert groups[0].status_reason == "same_book"
    assert groups[0].primary_filename == "primary.epub"
