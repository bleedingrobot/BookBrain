# Task 18 — Nightly SQLite backup to Drive

`backend/epub_librarian.db` (~18 MB) holds 2,200 books of resolved metadata
**and every human `/correct` ever made**, on one disk, with only a lossy Sheets
export as a fallback. Back it up.

Backend + a small Settings block. No AI. Ships by running locally (backend isn't
deployed) — build + test before committing.

## First read

- `prompts/README.md` (shared context, commands).
- `backend/app/jobs/nightly.py` — `run_nightly` is where the step goes;
  `_pull_local_folder` is the best-effort-phase pattern to copy.
- `backend/app/services/library_index_service.py` — the "write a file into the
  Drive library folder" pattern (`DriveProvider.upload_new_file`,
  `list_folders`, `create_folder`).
- `backend/app/data/db.py` — the DB is **WAL mode**, so a plain file copy can
  miss the `-wal` contents. Use `VACUUM INTO`.
- Memory `project-bookbrain` (repo layout, running dev servers).

## Goal

### A. `app/services/backup_service.py`

- `_sqlite_path() -> Path | None` — parse `settings.database_url`
  (`sqlite+aiosqlite:///./epub_librarian.db` → `./epub_librarian.db`, resolved
  from CWD). `:memory:` → `None` (backup is a no-op — tests won't try).
- `create_backup(creds, library_folder_id, *, retention=7) -> BackupResult`:
  1. Resolve/create a `backups/` folder under `library_folder_id`.
  2. `VACUUM INTO` a fresh temp path (consistent, compacted, folds in the WAL,
     safe while the app runs — it's a read). Run in a thread (`sqlite3`, not
     aiosqlite — one statement).
  3. gzip the snapshot → `epub_librarian-YYYY-MM-DD.db.gz`.
  4. **Also** `sqlite3.connect(snapshot).iterdump()` → gzip →
     `epub_librarian-YYYY-MM-DD.sql.gz` (portable SQL text — survives the app
     rotting; grep-able). Dump the snapshot, not the live DB, so both artifacts
     are the same instant.
  5. `provider.upload_new_file(name=…, data=…, parent_id=backups_folder,
     mime_type="application/gzip")` for each.
  6. List `backups/`, sort the `.db.gz` files newest-first, `trash_file` the
     `.db.gz` + its matching `.sql.gz` beyond `retention`.
  7. Return `{db_name, total_bytes, kept, trashed}`.
- `list_backups(creds, library_folder_id) -> list[BackupInfo]` — the `backups/`
  folder contents (name, size, createdTime, webViewLink), `.db.gz` only,
  newest first.
- Schema: `app/schemas/backup.py` — `BackupResult`, `BackupInfo`.
- `config.py`: `backup_retention: int = 7`.

### B. Routes (`app/api/routes/library.py`, mirror `/index`)

- `POST /api/library/backup` → `BackupResult` (needs creds + a configured
  library folder; 400/401 like `/index`). Works regardless of whether the
  nightly job is enabled.
- `GET /api/library/backups` → `list[BackupInfo]`.

### C. Nightly hook (`app/jobs/nightly.py`)

In `run_nightly`, inside `if library_folder_id:`, **first** (before covers —
snapshot the good state before the night's mutations):

```python
try:
    r = await backup_service.create_backup(creds, library_folder_id,
                                           retention=get_settings().backup_retention)
    steps.append(f"backup: {r.db_name} ({r.total_bytes // 1024} KB, kept {r.kept})")
except Exception as exc:
    logger.exception("nightly backup failed")
    steps.append(f"backup: FAILED — {exc}")
```

Best-effort — a Drive hiccup must not abort the scan. No toggle in v1 (the
nightly job is already opt-in; the manual button covers ad-hoc). Note in the
prompt: add a `backup_to_drive` setting if James wants nightly-without-backup.

### D. Settings page (`frontend/src/pages/Settings.tsx`)

A "Backups" block under the "Nightly run" section:
- `api.listBackups()` — a short list: date · size · a "Download" link
  (`webViewLink`).
- "Back up now" button → `api.createBackup()` → refetch the list; show the
  result line.
- If the newest backup is > 2 days old (or there are none), a subtle amber
  "last backup: N days ago" / "no backups yet".

### E. `RESTORE.md` (repo root)

Short: stop the backend, `gunzip epub_librarian-DATE.db.gz`, replace
`backend/epub_librarian.db`, delete `epub_librarian.db-wal` / `-shm`, restart.
Mention the `.sql.gz` is `sqlite3 new.db < dump.sql` if the binary won't open.

## Gotchas

- `VACUUM INTO` needs the target path to **not exist or be empty** — use a
  `tempfile` dir and a path inside it; clean up after.
- The standalone `python -m app.jobs.nightly` path gets the backup for free
  (it calls `run_nightly`) — nothing extra there.
- Don't back up to a folder the viewer's index sync will trip over — `backups/`
  is a plain subfolder, `library_index_service` only looks at `covers/` and the
  index file, and `librarySync` filters to ebook extensions, so a `.gz` in a
  subfolder is invisible to both. Confirm.
- `_sqlite_path` must handle both `sqlite:///` and `sqlite+aiosqlite:///`.
- No migration (no schema change).

## Acceptance

- `POST /api/library/backup` against a real temp DB (test with a fake provider)
  uploads a valid gzipped snapshot + a `.sql.gz`, and `gunzip | sqlite3` opens
  it with the same row counts.
- Retention: a 9th backup trashes the 2 oldest `.db.gz` + their `.sql.gz`.
- `run_nightly` includes a `backup: …` step; a raising `create_backup` leaves
  the run `ok=True` with `backup: FAILED …` in the summary.
- `_sqlite_path` unit-tested for both URL forms + `:memory:`.

## Ship it green

```
cd backend && pytest
cd frontend && npm run build
```

One commit. Update `ROADMAP.md` (move "DB backup to Drive" to Done) and
`prompts/README.md`. Memory: a short `project-bookbrain-db-backup` note.
