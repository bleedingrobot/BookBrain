from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.data.models import File, FileStatus, FileStatusReason
from app.services.duplicate_service import clear_duplicates, detect_same_book_duplicates, list_duplicate_groups


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
