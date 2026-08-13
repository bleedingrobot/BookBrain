import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from app.data.db import Base
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

from app.services.organize_service import FolderPathCache, OrganizeService, build_target_path


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


async def test_folder_path_cache_creates_missing_folders() -> None:
    provider = _FakeFolderProvider()
    cache = FolderPathCache()

    folder_id = await cache.resolve(provider, "root-id", ["Author", "Series"])

    assert len(provider.create_calls) == 2
    assert provider.create_calls[0] == ("Author", "root-id")
    author_folder_id = provider.folders["root-id"][0]["id"]
    assert provider.create_calls[1] == ("Series", author_folder_id)
    assert folder_id == provider.folders[author_folder_id][0]["id"]


async def test_folder_path_cache_reuses_existing_folder() -> None:
    provider = _FakeFolderProvider()
    provider.folders["root-id"] = [{"id": "existing-author", "name": "Author"}]
    cache = FolderPathCache()

    folder_id = await cache.resolve(provider, "root-id", ["Author"])

    assert folder_id == "existing-author"
    assert provider.create_calls == []


async def test_folder_path_cache_only_creates_a_shared_folder_once_under_concurrency() -> None:
    # Regression: without the cache/lock, two files organizing into the
    # same not-yet-existing folder at once could both see "not found" and
    # both create it — Drive doesn't enforce folder name uniqueness, so
    # that's a silent duplicate-folder bug, not an error.
    provider = _FakeFolderProvider()
    cache = FolderPathCache()

    results = await asyncio.gather(
        *(cache.resolve(provider, "root-id", ["Author", "Series"]) for _ in range(8))
    )

    assert len(set(results)) == 1
    assert len(provider.create_calls) == 2  # "Author" once, "Series" once — not 16


async def test_folder_path_cache_hit_does_not_touch_the_lock() -> None:
    provider = _FakeFolderProvider()
    cache = FolderPathCache()
    await cache.resolve(provider, "root-id", ["Author"])
    create_calls_after_warmup = len(provider.create_calls)

    await cache.resolve(provider, "root-id", ["Author"])

    assert len(provider.create_calls) == create_calls_after_warmup


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


async def test_organize_eligible_files_runs_concurrently_and_shares_one_new_folder(
    monkeypatch, tmp_path
) -> None:
    # organize_eligible_files gives each file its own DB session (a shared
    # AsyncSession isn't safe across concurrent coroutines), so this needs
    # a real file-backed DB rather than the usual :memory: fixture — SQLite
    # :memory: databases are per-connection, and separate sessions
    # wouldn't see each other's data.
    import app.services.organize_service as organize_module

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'organize.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as seed:
        author = Author(name="Brandon Sanderson")
        seed.add(author)
        await seed.flush()
        for i in range(5):
            book = Book(canonical_title=f"Book {i}", author_id=author.id)
            seed.add(book)
            await seed.flush()
            seed.add(
                File(
                    drive_file_id=f"drive-{i}",
                    drive_parent_id="inbox",
                    filename=f"book{i}.epub",
                    sha256=str(i) * 16,
                    size_bytes=100,
                    status=FileStatus.inbox,
                    book_id=book.id,
                )
            )
        await seed.commit()

    class _CM:
        async def __aenter__(self):
            self._session = session_factory()
            return self._session

        async def __aexit__(self, *args):
            await self._session.close()
            return False

    monkeypatch.setattr(organize_module, "async_session_factory", lambda: _CM())

    provider = _FakeMoveProvider()
    service = OrganizeService()

    counts = await service.organize_eligible_files(
        provider=provider, library_root_folder_id="lib-root", dry_run=False
    )

    assert counts == {"organized": 5, "failed": 0}
    assert len(provider.move_calls) == 5
    # All 5 books share one not-yet-existing author folder — must be
    # created exactly once, not once per concurrently-organized file.
    assert provider.create_calls == [("Brandon Sanderson", "lib-root")]

    async with session_factory() as check:
        statuses = (await check.execute(select(File.status))).scalars().all()
        assert all(s == FileStatus.organised for s in statuses)

    await engine.dispose()
