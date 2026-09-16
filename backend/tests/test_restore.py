import argparse
import gzip
import sqlite3
from pathlib import Path

import pytest

from app.core.config import get_settings
from app.jobs import restore


def _make_db(path: Path, titles=("Dune", "Neuromancer")) -> None:
    con = sqlite3.connect(path)
    con.executescript("CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT);")
    con.executemany("INSERT INTO books (title) VALUES (?)", [(t,) for t in titles])
    con.commit()
    con.close()


@pytest.fixture
def live_db(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "epub_librarian.db"
    _make_db(path, ("Old Book",))
    monkeypatch.setattr(get_settings(), "database_url", f"sqlite+aiosqlite:///{path}")
    return path


def _db_gz(tmp_path, titles) -> bytes:
    src = tmp_path / "src.db"
    _make_db(src, titles)
    data = gzip.compress(src.read_bytes())
    src.unlink()
    return data


def _sql_gz(tmp_path, titles) -> bytes:
    src = tmp_path / "src2.db"
    _make_db(src, titles)
    con = sqlite3.connect(src)
    dump = "\n".join(con.iterdump())
    con.close()
    src.unlink()
    return gzip.compress(dump.encode())


def test_materialise_prefers_the_binary(tmp_path) -> None:
    out = restore._materialise(_db_gz(tmp_path, ("A", "B", "C")), None, tmp_path / "r.db")
    con = sqlite3.connect(out)
    assert con.execute("SELECT count(*) FROM books").fetchone()[0] == 3
    con.close()


def test_materialise_falls_back_to_sql_when_the_binary_is_corrupt(tmp_path) -> None:
    bad = gzip.compress(b"PK\x03\x04 not really a database")
    out = restore._materialise(bad, _sql_gz(tmp_path, ("X", "Y")), tmp_path / "r.db")
    con = sqlite3.connect(out)
    assert con.execute("SELECT count(*) FROM books").fetchone()[0] == 2
    con.close()


def test_do_restore_replaces_the_db_and_keeps_a_pre_restore_copy(live_db, tmp_path) -> None:
    restore._do_restore(_db_gz(tmp_path, ("New 1", "New 2", "New 3")), None, "test", assume_yes=True)

    con = sqlite3.connect(live_db)
    assert con.execute("SELECT count(*) FROM books").fetchone()[0] == 3
    con.close()

    pre = list(live_db.parent.glob("epub_librarian.db.pre-restore-*"))
    assert len(pre) == 1
    con = sqlite3.connect(pre[0])
    assert con.execute("SELECT title FROM books").fetchone()[0] == "Old Book"
    con.close()


def test_do_restore_aborts_if_the_db_is_locked(live_db, tmp_path) -> None:
    holder = sqlite3.connect(live_db, isolation_level=None)
    holder.execute("BEGIN EXCLUSIVE")
    with pytest.raises(SystemExit):
        restore._do_restore(_db_gz(tmp_path, ("x",)), None, "t", assume_yes=True)
    holder.execute("ROLLBACK")
    holder.close()

    # the live DB is untouched — no pre-restore copy, old data intact
    assert list(live_db.parent.glob("epub_librarian.db.pre-restore-*")) == []
    con = sqlite3.connect(live_db)
    assert con.execute("SELECT title FROM books").fetchone()[0] == "Old Book"
    con.close()


def test_do_restore_bails_on_a_corrupt_source_with_no_fallback(live_db, tmp_path) -> None:
    with pytest.raises(SystemExit):
        restore._do_restore(gzip.compress(b"garbage"), None, "t", assume_yes=True)


def test_file_path_router_rejects_unknown_extensions(tmp_path) -> None:
    junk = tmp_path / "notes.txt"
    junk.write_text("hi")
    with pytest.raises(SystemExit):
        restore._run(argparse.Namespace(file=str(junk), yes=True, list=False, latest=False, date=None))
