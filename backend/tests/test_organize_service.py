from sqlalchemy import select

from app.data.models import (
    Author,
    Book,
    File,
    FileStatus,
    Identifier,
    IdentifierType,
    Operation,
    Series,
)
from sqlalchemy.orm import selectinload

from app.services.organize_service import OrganizeService, _ensure_folder_path, build_target_path


def test_build_target_path_with_series() -> None:
    folders, filename = build_target_path(
        title="Dune", author_name="Frank Herbert", series_name="Dune Chronicles", series_number=1.0
    )

    assert folders == ["Frank Herbert", "Dune Chronicles"]
    assert filename == "Frank Herbert, Dune, Dune Chronicles, 1.epub"


def test_build_target_path_without_series() -> None:
    folders, filename = build_target_path(
        title="Neuromancer", author_name="William Gibson", series_name=None, series_number=None
    )

    assert folders == ["William Gibson"]
    assert filename == "William Gibson, Neuromancer.epub"


def test_build_target_path_no_author() -> None:
    folders, filename = build_target_path(
        title="Anonymous", author_name=None, series_name=None, series_number=None
    )

    assert folders == []
    assert filename == "Anonymous.epub"


def test_build_target_path_sanitizes_invalid_characters() -> None:
    folders, filename = build_target_path(
        title="Weird: Title / Name?", author_name="A/B: Author", series_name=None, series_number=None
    )

    assert "/" not in folders[0]
    assert ":" not in folders[0]
    assert "/" not in filename
    assert ":" not in filename


def test_build_target_path_fractional_series_number() -> None:
    _, filename = build_target_path(
        title="Novella", author_name="Author", series_name="Series", series_number=2.5
    )

    assert filename == "Author, Novella, Series, 2.5.epub"


def test_build_target_path_series_without_series_number() -> None:
    _, filename = build_target_path(
        title="Novella", author_name="Author", series_name="Series", series_number=None
    )

    assert filename == "Author, Novella, Series.epub"


class _FakeFolderProvider:
    def __init__(self) -> None:
        self.folders: dict[str, list[dict]] = {}
        self.create_calls: list[tuple[str, str | None]] = []
        self._next_id = 0

    def list_folders(self, parent_id: str | None) -> list[dict]:
        return self.folders.get(parent_id or "root", [])

    def create_folder(self, name: str, parent_id: str | None = None) -> dict:
        self._next_id += 1
        folder = {"id": f"folder-{self._next_id}", "name": name}
        self.folders.setdefault(parent_id or "root", []).append(folder)
        self.create_calls.append((name, parent_id))
        return folder


def test_ensure_folder_path_creates_missing_folders() -> None:
    provider = _FakeFolderProvider()

    folder_id = _ensure_folder_path(provider, "root-id", ["Author", "Series"])

    assert len(provider.create_calls) == 2
    assert provider.create_calls[0] == ("Author", "root-id")
    author_folder_id = provider.folders["root-id"][0]["id"]
    assert provider.create_calls[1] == ("Series", author_folder_id)
    assert folder_id == provider.folders[author_folder_id][0]["id"]


def test_ensure_folder_path_reuses_existing_folder() -> None:
    provider = _FakeFolderProvider()
    provider.folders["root-id"] = [{"id": "existing-author", "name": "Author"}]

    folder_id = _ensure_folder_path(provider, "root-id", ["Author"])

    assert folder_id == "existing-author"
    assert provider.create_calls == []


class _FakeMoveProvider(_FakeFolderProvider):
    def __init__(self) -> None:
        super().__init__()
        self.move_calls: list[dict] = []

    def move_and_rename(self, file_id, *, old_parent_id, new_parent_id, new_name) -> dict:
        self.move_calls.append(
            {
                "file_id": file_id,
                "old_parent_id": old_parent_id,
                "new_parent_id": new_parent_id,
                "new_name": new_name,
            }
        )
        return {"id": file_id, "name": new_name, "parents": [new_parent_id]}


async def _seed_file(db_session, *, author="Frank Herbert", series=None, series_number=None) -> File:
    author_row = Author(name=author) if author else None
    if author_row:
        db_session.add(author_row)
        await db_session.flush()

    series_row = Series(name=series) if series else None
    if series_row:
        db_session.add(series_row)
        await db_session.flush()

    book = Book(
        canonical_title="Dune",
        author_id=author_row.id if author_row else None,
        series_id=series_row.id if series_row else None,
        series_number=series_number,
    )
    db_session.add(book)
    await db_session.flush()
    db_session.add(Identifier(book_id=book.id, type=IdentifierType.isbn13, value="9780441172719"))

    file_row = File(
        drive_file_id="drive-1",
        drive_parent_id="inbox-parent",
        filename="dune.epub",
        sha256="abc123",
        size_bytes=100,
        status=FileStatus.inbox,
        book_id=book.id,
    )
    db_session.add(file_row)
    await db_session.commit()

    result = await db_session.execute(
        select(File)
        .where(File.id == file_row.id)
        .options(selectinload(File.book).selectinload(Book.author))
        .options(selectinload(File.book).selectinload(Book.series))
    )
    return result.scalar_one()


async def test_organize_file_dry_run_does_not_touch_drive_or_file_status(db_session) -> None:
    file_row = await _seed_file(db_session)
    service = OrganizeService()

    operation = await service._organize_file(
        db_session, file_row, provider=None, library_root_folder_id=None, dry_run=True
    )

    assert operation.dry_run is True
    assert operation.new_name == "Frank Herbert, Dune.epub"
    assert operation.new_parent_id == "Frank Herbert"

    await db_session.refresh(file_row)
    assert file_row.status.value == "inbox"
    assert file_row.filename == "dune.epub"
    assert file_row.drive_parent_id == "inbox-parent"


async def test_organize_file_real_run_moves_and_updates_file(db_session) -> None:
    file_row = await _seed_file(db_session)
    provider = _FakeMoveProvider()
    service = OrganizeService()

    operation = await service._organize_file(
        db_session, file_row, provider=provider, library_root_folder_id="lib-root", dry_run=False
    )

    assert operation.dry_run is False
    assert len(provider.move_calls) == 1
    move = provider.move_calls[0]
    assert move["file_id"] == "drive-1"
    assert move["old_parent_id"] == "inbox-parent"
    assert move["new_name"] == "Frank Herbert, Dune.epub"

    await db_session.refresh(file_row)
    assert file_row.status.value == "organised"
    assert file_row.filename == "Frank Herbert, Dune.epub"
    assert file_row.drive_parent_id == move["new_parent_id"]


async def test_organize_file_real_run_reuses_existing_author_folder(db_session) -> None:
    file_row = await _seed_file(db_session, series="Dune Chronicles", series_number=1.0)
    provider = _FakeMoveProvider()
    provider.folders["lib-root"] = [{"id": "author-folder", "name": "Frank Herbert"}]
    service = OrganizeService()

    await service._organize_file(
        db_session, file_row, provider=provider, library_root_folder_id="lib-root", dry_run=False
    )

    assert provider.create_calls == [("Dune Chronicles", "author-folder")]


async def test_run_organize_skips_non_inbox_files(db_session, monkeypatch) -> None:
    import app.services.organize_service as organize_module

    async def fake_session_factory():
        return db_session

    class _CM:
        async def __aenter__(self):
            return db_session

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(organize_module, "async_session_factory", lambda: _CM())

    file_row = await _seed_file(db_session)
    file_row.status = FileStatus.review
    await db_session.commit()

    service = OrganizeService()
    job = service.create_job()
    await service.run_organize(job.job_id, None, None, True)

    status = service.get_status(job.job_id)
    assert "0 organized" in status.detail

    operations = (await db_session.execute(select(Operation))).scalars().all()
    assert operations == []
