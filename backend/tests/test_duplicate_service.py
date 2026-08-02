from app.data.models import File, FileStatus
from app.services.duplicate_service import list_duplicate_groups


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
