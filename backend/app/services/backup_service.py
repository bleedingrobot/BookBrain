"""Nightly SQLite backup to Google Drive.

`epub_librarian.db` holds every book's resolved metadata and every human
`/correct` ever made — one disk, no real backup. This drops a dated, gzipped
snapshot (plus a portable SQL dump) into a `backups/` subfolder of the Drive
library folder on each nightly run, keeping the last `settings.backup_retention`.

The DB is WAL-mode, so a plain file copy can miss the `-wal` contents. We use
`VACUUM INTO`, which produces a consistent, compacted copy (folding in the WAL)
and is safe while the app is running — it only takes a read lock.
"""

import asyncio
import gzip
import logging
import re
import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from google.oauth2.credentials import Credentials

from app.core.config import get_settings
from app.providers.drive.client import build_drive_service
from app.providers.drive.provider import DriveProvider
from app.schemas.backup import BackupInfo, BackupResult

logger = logging.getLogger(__name__)

_BACKUPS_FOLDER = "backups"
_STEM = "epub_librarian"
_NAME_RE = re.compile(rf"^{re.escape(_STEM)}-(\d{{4}}-\d{{2}}-\d{{2}}(?:T\d{{6}})?)\.db\.gz$")
_GZIP_MIME = "application/gzip"


def _sqlite_path() -> Path | None:
    """The on-disk path from `settings.database_url`, or None for an in-memory
    DB (tests) — a backup is then a no-op."""
    url = get_settings().database_url
    for prefix in ("sqlite+aiosqlite:///", "sqlite:///"):
        if url.startswith(prefix):
            rest = url[len(prefix) :]
            if ":memory:" in rest or rest == "":
                return None
            return Path(rest).resolve()
    return None


def _vacuum_into(src: Path, dest: Path) -> None:
    con = sqlite3.connect(str(src))
    try:
        con.execute("PRAGMA busy_timeout=10000")
        con.execute("VACUUM INTO ?", (str(dest),))
    finally:
        con.close()


def _sql_dump_gz(snapshot: Path) -> bytes:
    con = sqlite3.connect(str(snapshot))
    try:
        text = "\n".join(con.iterdump())
    finally:
        con.close()
    return gzip.compress(text.encode("utf-8"))


def _build_snapshots(db_path: Path) -> tuple[bytes, bytes]:
    """(db.gz, sql.gz) for the live DB, taken at one consistent instant."""
    with tempfile.TemporaryDirectory(prefix="bookbrain-backup-") as tmp:
        snapshot = Path(tmp) / "snapshot.db"
        _vacuum_into(db_path, snapshot)
        db_gz = gzip.compress(snapshot.read_bytes())
        sql_gz = _sql_dump_gz(snapshot)
    return db_gz, sql_gz


def _resolve_backups_folder(provider: DriveProvider, library_folder_id: str) -> str:
    existing = next(
        (f for f in provider.list_folders(library_folder_id) if f["name"] == _BACKUPS_FOLDER),
        None,
    )
    if existing is not None:
        return existing["id"]
    return provider.create_folder(_BACKUPS_FOLDER, parent_id=library_folder_id)["id"]


def _prune(provider: DriveProvider, files: list[dict], retention: int) -> tuple[int, int]:
    """Trash `.db.gz` snapshots (and their sibling `.sql.gz`) beyond the newest
    `retention`. Returns (kept, trashed)."""
    by_name = {f["name"]: f for f in files}
    dated = sorted(
        (m.group(1), name) for name, f in by_name.items() if (m := _NAME_RE.match(name))
    )
    stale = dated[: max(0, len(dated) - retention)]
    trashed = 0
    for date, db_name in stale:
        for name in (db_name, f"{_STEM}-{date}.sql.gz"):
            hit = by_name.get(name)
            if hit is not None:
                provider.trash_file(hit["id"])
                trashed += 1
    return len(dated) - len(stale), trashed


def _do_backup(creds: Credentials, library_folder_id: str, retention: int) -> BackupResult:
    db_path = _sqlite_path()
    if db_path is None or not db_path.exists():
        raise RuntimeError(f"no on-disk SQLite database to back up ({db_path})")

    db_gz, sql_gz = _build_snapshots(db_path)

    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    db_name = f"{_STEM}-{stamp}.db.gz"
    sql_name = f"{_STEM}-{stamp}.sql.gz"

    provider = DriveProvider(build_drive_service(creds))
    folder_id = _resolve_backups_folder(provider, library_folder_id)

    # Replace a same-day run rather than leaving two files with one name.
    for f in provider.list_files_in_folder(folder_id):
        if f["name"] in (db_name, sql_name):
            provider.trash_file(f["id"])

    provider.upload_new_file(name=db_name, data=db_gz, parent_id=folder_id, mime_type=_GZIP_MIME)
    provider.upload_new_file(name=sql_name, data=sql_gz, parent_id=folder_id, mime_type=_GZIP_MIME)

    kept, trashed = _prune(provider, provider.list_files_in_folder(folder_id), retention)
    logger.info("backup: uploaded %s (%d KB), kept %d, trashed %d", db_name,
                (len(db_gz) + len(sql_gz)) // 1024, kept, trashed)
    return BackupResult(
        db_name=db_name, total_bytes=len(db_gz) + len(sql_gz), kept=kept, trashed=trashed
    )


async def create_backup(
    creds: Credentials, library_folder_id: str, *, retention: int | None = None
) -> BackupResult:
    r = retention if retention is not None else get_settings().backup_retention
    return await asyncio.to_thread(_do_backup, creds, library_folder_id, r)


def list_snapshot_files(provider: DriveProvider, library_folder_id: str) -> list[dict]:
    """Every dated `.db.gz` in `backups/`, newest first, as raw dicts with the
    Drive id kept (restore needs it): `{date, db_id, db_name, size, sql_id,
    sql_name}`. `sql_id` is None if the sibling `.sql.gz` is missing."""
    folder = next(
        (f for f in provider.list_folders(library_folder_id) if f["name"] == _BACKUPS_FOLDER),
        None,
    )
    if folder is None:
        return []
    files = provider.list_files_in_folder(folder["id"])
    by_name = {f["name"]: f for f in files}
    out: list[dict] = []
    for name, f in by_name.items():
        m = _NAME_RE.match(name)
        if m is None:
            continue
        sql = by_name.get(f"{_STEM}-{m.group(1)}.sql.gz")
        out.append(
            {
                "date": m.group(1),
                "db_id": f["id"],
                "db_name": name,
                "size": int(f.get("size") or 0),
                "sql_id": sql["id"] if sql else None,
                "sql_name": sql["name"] if sql else None,
            }
        )
    out.sort(key=lambda s: s["date"], reverse=True)
    return out


def _do_list(creds: Credentials, library_folder_id: str) -> list[BackupInfo]:
    provider = DriveProvider(build_drive_service(creds))
    return [
        BackupInfo(
            name=s["db_name"],
            size_bytes=s["size"],
            created_at=s["date"],
            view_url=f"https://drive.google.com/file/d/{s['db_id']}/view",
        )
        for s in list_snapshot_files(provider, library_folder_id)
    ]


async def list_backups(creds: Credentials, library_folder_id: str) -> list[BackupInfo]:
    return await asyncio.to_thread(_do_list, creds, library_folder_id)
