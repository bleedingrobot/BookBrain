"""Restore epub_librarian.db from a backup.

    python -m app.jobs.restore --list           # show what's available
    python -m app.jobs.restore --latest         # restore the newest Drive backup
    python -m app.jobs.restore --date 2026-09-07
    python -m app.jobs.restore --file some-backup.db.gz   # a local file
    python -m app.jobs.restore                   # interactive picker

This REPLACES the live database. The current one is renamed to
`epub_librarian.db.pre-restore-<timestamp>` first, so a mistaken restore is
itself undoable. **Stop the BookBrain backend before running this** — the
script refuses to touch the DB while anything else has it open.

See backup_service / `RESTORE.md` for what a backup is.
"""

import argparse
import gzip
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from google.auth.exceptions import GoogleAuthError

from app.data.db import async_session_factory
from app.data.repositories.settings_repository import SettingsRepository
from app.providers.drive.client import build_drive_service
from app.providers.drive.provider import DriveProvider
from app.services import backup_service
from app.services.auth_service import get_auth_service
from app.services.drive_service import DriveService

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_CORE_TABLES = ("books", "authors", "series", "files", "reviews", "operations")


def _fail(msg: str) -> None:
    print(f"\n  ✗ {msg}\n", file=sys.stderr)
    raise SystemExit(1)


def _db_path() -> Path:
    p = backup_service._sqlite_path()
    if p is None:
        _fail("DATABASE_URL is not an on-disk SQLite database — nothing to restore.")
    return p


def _counts(db: Path) -> str:
    if not db.exists():
        return "(no current database)"
    con = sqlite3.connect(str(db))
    try:
        parts = []
        for t in _CORE_TABLES:
            try:
                parts.append(f"{t} {con.execute(f'SELECT count(*) FROM {t}').fetchone()[0]}")
            except sqlite3.Error:
                pass
        return ", ".join(parts) or "(unreadable)"
    finally:
        con.close()


def _assert_db_free(db: Path) -> None:
    if not db.exists():
        return
    con = sqlite3.connect(str(db), timeout=1)
    try:
        con.execute("PRAGMA busy_timeout=1000")
        con.execute("BEGIN EXCLUSIVE")
        con.execute("ROLLBACK")
    except sqlite3.OperationalError:
        _fail(
            "The database is in use. Stop the BookBrain backend first — close the "
            "uvicorn window and kill any stray `uvicorn --reload` workers — then retry."
        )
    finally:
        con.close()


def _integrity_ok(db: Path) -> bool:
    try:
        con = sqlite3.connect(str(db))
        try:
            return con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        finally:
            con.close()
    except sqlite3.DatabaseError:
        return False


def _materialise(db_gz: bytes | None, sql_gz: bytes | None, into: Path) -> Path:
    """Write a usable .db at `into` from a gzipped db and/or a gzipped SQL
    dump. Prefers the binary; falls back to rebuilding from SQL if the binary
    is missing or fails its integrity check."""
    if db_gz is not None:
        into.write_bytes(gzip.decompress(db_gz))
        if _integrity_ok(into):
            return into
        print("  ! the .db.gz failed its integrity check", end="")
        if sql_gz is None:
            _fail("and there is no .sql.gz fallback.")
        print(" — rebuilding from the .sql.gz dump instead")
        into.unlink()

    if sql_gz is None:
        _fail("nothing to restore from.")
    con = sqlite3.connect(str(into))
    try:
        con.executescript(gzip.decompress(sql_gz).decode("utf-8"))
        con.commit()
    finally:
        con.close()
    if not _integrity_ok(into):
        _fail("the rebuilt database also failed its integrity check.")
    return into


async def _drive_provider() -> tuple[DriveProvider, str]:
    async with async_session_factory() as session:
        repo = SettingsRepository(session)
        try:
            creds = await get_auth_service().get_credentials(repo)
        except GoogleAuthError:
            creds = None
        if creds is None:
            _fail("not connected to Google Drive — reconnect in the app, or use --file.")
        library = await DriveService.get_library_folder_config(repo)
    if library is None:
        _fail("no library folder is configured.")
    return DriveProvider(build_drive_service(creds)), library.folder_id


async def _list_drive() -> list[dict]:
    provider, folder_id = await _drive_provider()
    return backup_service.list_snapshot_files(provider, folder_id)


async def _fetch(snapshot: dict) -> tuple[bytes, bytes | None]:
    provider, _ = await _drive_provider()
    db_gz = provider.download_file(snapshot["db_id"])
    sql_gz = provider.download_file(snapshot["sql_id"]) if snapshot.get("sql_id") else None
    return db_gz, sql_gz


def _do_restore(db_gz: bytes | None, sql_gz: bytes | None, label: str, assume_yes: bool) -> None:
    db = _db_path()
    _assert_db_free(db)  # fail fast, before decompressing / prompting

    with tempfile.TemporaryDirectory(prefix="bookbrain-restore-") as tmp:
        staged = _materialise(db_gz, sql_gz, Path(tmp) / "restored.db")

        print(f"\n  Restore source : {label}")
        print(f"  Would become   : {_counts(staged)}")
        print(f"  Current DB     : {_counts(db)}")
        if db.exists():
            keep = db.with_name(db.name + f".pre-restore-{datetime.now():%Y%m%dT%H%M%S}")
            print(f"  Current DB saved to: {keep.name}")

        if not assume_yes:
            if input('\n  Type "restore" to proceed: ').strip().lower() != "restore":
                _fail("cancelled.")

        _assert_db_free(db)  # re-check — the prompt was a pause

        if db.exists():
            db.rename(keep)
        for side in ("-wal", "-shm"):
            p = db.with_name(db.name + side)
            if p.exists():
                p.unlink()
        shutil.move(str(staged), str(db))

    if not _integrity_ok(db):
        _fail("restored database failed its integrity check — check the .pre-restore file.")
    print(f"\n  ✓ Restored. Now: {_counts(db)}")
    print("    Start the BookBrain backend.\n")


def _pick_interactively(snapshots: list[dict]) -> dict:
    print("\n  Available backups:\n")
    for i, s in enumerate(snapshots, 1):
        mark = "" if s.get("sql_id") else "  (no .sql.gz fallback)"
        print(f"   {i:2}. {s['date']}   {s['size'] // 1024} KB{mark}")
    raw = input("\n  Number to restore (blank to cancel): ").strip()
    if not raw.isdigit() or not (1 <= int(raw) <= len(snapshots)):
        _fail("cancelled.")
    return snapshots[int(raw) - 1]


def _run(args: argparse.Namespace) -> None:
    import asyncio

    if args.file:
        path = Path(args.file)
        if not path.exists():
            _fail(f"no such file: {path}")
        data = path.read_bytes()
        if path.name.endswith(".sql.gz"):
            _do_restore(None, data, path.name, args.yes)
        elif path.name.endswith(".db.gz"):
            _do_restore(data, None, path.name, args.yes)
        elif path.name.endswith(".db"):
            _do_restore(gzip.compress(data), None, path.name, args.yes)
        else:
            _fail("--file must be a .db.gz, .sql.gz, or .db")
        return

    snapshots = asyncio.run(_list_drive())
    if not snapshots:
        _fail("no backups found in Drive.")

    if args.list:
        print("\n  Backups in Drive (newest first):\n")
        for s in snapshots:
            print(f"   {s['date']}   {s['size'] // 1024} KB   {s['db_name']}")
        print()
        return

    if args.latest:
        chosen = snapshots[0]
    elif args.date:
        chosen = next((s for s in snapshots if s["date"] == args.date), None)
        if chosen is None:
            _fail(f"no backup dated {args.date}. Try --list.")
    else:
        chosen = _pick_interactively(snapshots)

    db_gz, sql_gz = asyncio.run(_fetch(chosen))
    _do_restore(db_gz, sql_gz, f"{chosen['date']} (Drive)", args.yes)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--list", action="store_true", help="show available backups and exit")
    g.add_argument("--latest", action="store_true", help="restore the newest Drive backup")
    g.add_argument("--date", metavar="YYYY-MM-DD", help="restore the backup from this date")
    g.add_argument("--file", metavar="PATH", help="restore from a local .db.gz / .sql.gz / .db")
    ap.add_argument("--yes", action="store_true", help="skip the typed confirmation")
    try:
        _run(ap.parse_args())
    except SystemExit:
        raise
    except KeyboardInterrupt:
        print("\n  cancelled.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
