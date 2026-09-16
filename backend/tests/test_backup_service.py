import gzip
import sqlite3
from pathlib import Path

import pytest

from app.core.config import get_settings
from app.services import backup_service


class _FakeProvider:
    def __init__(self) -> None:
        self.folders: dict[str, list[dict]] = {}  # parent_id -> [{id,name}]
        self.files: dict[str, list[dict]] = {}  # folder_id -> [{id,name,size}]
        self.uploaded: list[dict] = []
        self.trashed: list[str] = []
        self._n = 0

    def _id(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix}-{self._n}"

    def list_folders(self, parent_id):
        return list(self.folders.get(parent_id, []))

    def create_folder(self, name, parent_id=None):
        folder = {"id": self._id("folder"), "name": name}
        self.folders.setdefault(parent_id, []).append(folder)
        self.files.setdefault(folder["id"], [])
        return folder

    def list_files_in_folder(self, folder_id):
        return list(self.files.get(folder_id, []))

    def upload_new_file(self, *, name, data, parent_id, mime_type):
        entry = {"id": self._id("file"), "name": name, "size": str(len(data))}
        self.files.setdefault(parent_id, []).append(entry)
        self.uploaded.append({"name": name, "data": data, "parent_id": parent_id})
        return entry

    def trash_file(self, file_id):
        self.trashed.append(file_id)
        for lst in self.files.values():
            lst[:] = [f for f in lst if f["id"] != file_id]


@pytest.fixture
def real_db(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "epub_librarian.db"
    con = sqlite3.connect(path)
    con.executescript(
        "CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT);"
        "INSERT INTO books (title) VALUES ('Dune'), ('Neuromancer'), ('It');"
    )
    con.commit()
    con.close()
    monkeypatch.setattr(get_settings(), "database_url", f"sqlite+aiosqlite:///{path}")
    return path


@pytest.fixture
def fake_provider(monkeypatch) -> _FakeProvider:
    fake = _FakeProvider()
    monkeypatch.setattr(backup_service, "build_drive_service", lambda _creds: None)
    monkeypatch.setattr(backup_service, "DriveProvider", lambda _svc: fake)
    return fake


@pytest.mark.parametrize(
    "url,expected_none",
    [
        ("sqlite+aiosqlite:///./epub_librarian.db", False),
        ("sqlite:///./x.db", False),
        ("sqlite+aiosqlite:///:memory:", True),
        ("postgresql://x", True),
    ],
)
def test_sqlite_path(monkeypatch, url, expected_none) -> None:
    monkeypatch.setattr(get_settings(), "database_url", url)
    assert (backup_service._sqlite_path() is None) is expected_none


async def test_create_backup_uploads_a_restorable_snapshot(real_db, fake_provider) -> None:
    result = await backup_service.create_backup(None, "lib-root", retention=7)

    names = sorted(u["name"] for u in fake_provider.uploaded)
    assert len(names) == 2
    assert names[0].endswith(".db.gz") and names[1].endswith(".sql.gz")
    assert result.db_name == names[0]
    assert result.total_bytes > 0

    db_gz = next(u["data"] for u in fake_provider.uploaded if u["name"].endswith(".db.gz"))
    restored = gzip.decompress(db_gz)
    tmp = real_db.parent / "restored.db"
    tmp.write_bytes(restored)
    con = sqlite3.connect(tmp)
    assert con.execute("SELECT count(*) FROM books").fetchone()[0] == 3
    con.close()

    # the .sql.gz is a working SQL dump
    sql_gz = next(u["data"] for u in fake_provider.uploaded if u["name"].endswith(".sql.gz"))
    sql = gzip.decompress(sql_gz).decode()
    assert "CREATE TABLE books" in sql and "Neuromancer" in sql


async def test_create_backup_creates_the_backups_folder_once(real_db, fake_provider) -> None:
    await backup_service.create_backup(None, "lib-root", retention=7)
    await backup_service.create_backup(None, "lib-root", retention=7)
    assert [f["name"] for f in fake_provider.folders["lib-root"]] == ["backups"]


async def test_same_day_rerun_replaces_rather_than_duplicates(real_db, fake_provider) -> None:
    await backup_service.create_backup(None, "lib-root", retention=7)
    await backup_service.create_backup(None, "lib-root", retention=7)
    folder_id = fake_provider.folders["lib-root"][0]["id"]
    names = [f["name"] for f in fake_provider.files[folder_id]]
    assert len(names) == len(set(names)) == 2  # one .db.gz + one .sql.gz, no dupes


async def test_retention_trashes_the_oldest(real_db, fake_provider) -> None:
    folder = fake_provider.create_folder("backups", parent_id="lib-root")
    for day in range(1, 10):  # 9 existing dated snapshots
        d = f"2026-08-{day:02d}"
        fake_provider.files[folder["id"]].append(
            {"id": f"old-db-{day}", "name": f"epub_librarian-{d}.db.gz", "size": "10"}
        )
        fake_provider.files[folder["id"]].append(
            {"id": f"old-sql-{day}", "name": f"epub_librarian-{d}.sql.gz", "size": "10"}
        )

    result = await backup_service.create_backup(None, "lib-root", retention=7)

    # 9 old + 1 new = 10; keep 7 → trash 3 days = 6 files
    assert result.kept == 7
    assert result.trashed == 6
    assert set(fake_provider.trashed) >= {"old-db-1", "old-sql-1", "old-db-2", "old-db-3"}


async def test_backup_raises_when_the_db_is_in_memory(monkeypatch, fake_provider) -> None:
    monkeypatch.setattr(get_settings(), "database_url", "sqlite+aiosqlite:///:memory:")
    with pytest.raises(RuntimeError):
        await backup_service.create_backup(None, "lib-root")


async def test_list_backups_reads_dated_snapshots_newest_first(real_db, fake_provider) -> None:
    folder = fake_provider.create_folder("backups", parent_id="lib-root")
    for name in ("epub_librarian-2026-08-01.db.gz", "epub_librarian-2026-08-03.db.gz",
                 "epub_librarian-2026-08-01.sql.gz", "notes.txt"):
        fake_provider.files[folder["id"]].append(
            {"id": f"id-{name}", "name": name, "size": "123"}
        )

    infos = await backup_service.list_backups(None, "lib-root")
    assert [i.created_at for i in infos] == ["2026-08-03", "2026-08-01"]
    assert infos[0].view_url and infos[0].view_url.startswith("https://drive.google.com/")
