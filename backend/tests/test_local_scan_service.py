from pathlib import Path

from sqlalchemy import select

from app.data.models import LocalFile, LocalFileStatus
from app.services import local_scan_service


class _FakeProvider:
    def __init__(self, fail_for: set[str] | None = None) -> None:
        self.uploads: list[tuple[str, bytes, str]] = []
        self._fail_for = fail_for or set()

    def upload_new_file(self, *, name: str, data: bytes, parent_id: str, mime_type: str) -> dict:
        if name in self._fail_for:
            raise RuntimeError("simulated upload failure")
        self.uploads.append((name, data, parent_id))
        return {"id": f"drive-{name}", "name": name, "parents": [parent_id]}


def _write(root: Path, relative: str, content: bytes = b"data") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


async def test_scan_local_folder_discovers_supported_extensions(db_session, tmp_path) -> None:
    _write(tmp_path, "book.epub")
    _write(tmp_path, "book.kpub")
    _write(tmp_path, "book.mobi")
    _write(tmp_path, "book.rtf")
    _write(tmp_path, "book.txt")
    _write(tmp_path, "cover.jpg")

    rows = await local_scan_service.scan_local_folder(db_session, str(tmp_path))

    assert {r.filename for r in rows} == {"book.epub", "book.kpub", "book.mobi", "book.rtf", "book.txt"}
    assert all(r.status == LocalFileStatus.pending for r in rows)


async def test_scan_local_folder_recurses_into_subfolders(db_session, tmp_path) -> None:
    _write(tmp_path, "top.epub")
    _write(tmp_path, "Series/Author/nested.epub")

    rows = await local_scan_service.scan_local_folder(db_session, str(tmp_path))

    assert {r.filename for r in rows} == {"top.epub", "nested.epub"}


async def test_scan_local_folder_does_not_reoffer_already_seen_paths(db_session, tmp_path) -> None:
    _write(tmp_path, "book.epub")

    first = await local_scan_service.scan_local_folder(db_session, str(tmp_path))
    assert len(first) == 1

    second = await local_scan_service.scan_local_folder(db_session, str(tmp_path))
    assert len(second) == 1
    assert second[0].id == first[0].id

    all_rows = (await db_session.execute(select(LocalFile))).scalars().all()
    assert len(all_rows) == 1  # no duplicate row created


async def test_scan_local_folder_missing_directory_returns_existing_pending(db_session) -> None:
    rows = await local_scan_service.scan_local_folder(db_session, "Z:\\does-not-exist")

    assert rows == []


async def test_copy_to_drive_uploads_and_marks_copied(db_session, tmp_path) -> None:
    path = _write(tmp_path, "book.epub", b"epub bytes")
    rows = await local_scan_service.scan_local_folder(db_session, str(tmp_path))
    provider = _FakeProvider()

    result = await local_scan_service.copy_to_drive(db_session, [rows[0].id], provider, "inbox-id")

    assert result == {"copied": 1, "failed": 0}
    assert provider.uploads == [("book.epub", b"epub bytes", "inbox-id")]

    updated = await db_session.get(LocalFile, rows[0].id)
    assert updated.status == LocalFileStatus.copied
    assert Path(path).exists()  # local file itself is untouched


async def test_copy_to_drive_counts_failures_without_crashing(db_session, tmp_path) -> None:
    _write(tmp_path, "good.epub")
    _write(tmp_path, "bad.epub")
    rows = await local_scan_service.scan_local_folder(db_session, str(tmp_path))
    provider = _FakeProvider(fail_for={"bad.epub"})

    result = await local_scan_service.copy_to_drive(
        db_session, [r.id for r in rows], provider, "inbox-id"
    )

    assert result == {"copied": 1, "failed": 1}
    statuses = {r.filename: r.status for r in (await db_session.execute(select(LocalFile))).scalars().all()}
    assert statuses["good.epub"] == LocalFileStatus.copied
    assert statuses["bad.epub"] == LocalFileStatus.pending  # left alone, retryable


async def test_dismiss_marks_dismissed_and_excludes_from_pending(db_session, tmp_path) -> None:
    _write(tmp_path, "book.epub")
    rows = await local_scan_service.scan_local_folder(db_session, str(tmp_path))

    count = await local_scan_service.dismiss(db_session, [rows[0].id])

    assert count == 1
    remaining = await local_scan_service.list_pending(db_session)
    assert remaining == []


async def test_dismissed_files_are_not_reoffered_on_rescan(db_session, tmp_path) -> None:
    _write(tmp_path, "book.epub")
    rows = await local_scan_service.scan_local_folder(db_session, str(tmp_path))
    await local_scan_service.dismiss(db_session, [rows[0].id])

    second = await local_scan_service.scan_local_folder(db_session, str(tmp_path))

    assert second == []
