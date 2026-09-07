# Restoring the database from a backup

BookBrain saves a dated snapshot of `backend/epub_librarian.db` to a `backups/`
folder in your Drive **library folder** — on each nightly run, on the separate
backup schedule (Settings → Backups), and whenever you click **Back up now**.
The last 7 are kept. Each is a gzipped SQLite database plus a portable SQL dump.

## The easy way — the restore script

**Stop the BookBrain backend first** (close its window; kill any stray
`uvicorn --reload` workers). The script refuses to run while anything has the
database open.

Then, from `backend/`:

```
.venv\Scripts\python -m app.jobs.restore --list        # see what's available
.venv\Scripts\python -m app.jobs.restore --latest      # restore the newest
.venv\Scripts\python -m app.jobs.restore --date 2026-09-07
.venv\Scripts\python -m app.jobs.restore               # interactive picker
```

or double-click **`backend/scripts/restore-backup.bat`**.

The script:

1. downloads the chosen backup from Drive (or `--file <path>` for a local one),
2. checks it opens cleanly — and rebuilds from the `.sql.gz` dump automatically
   if the binary is damaged,
3. shows you the before/after book counts and asks you to type `restore`
   (skip with `--yes`),
4. renames the current database to
   `epub_librarian.db.pre-restore-<timestamp>` — so a mistaken restore is
   itself undoable,
5. puts the backup in place and runs a final integrity check.

Then start the backend. If the OAuth token in the restored database is stale,
reconnect Google in Settings.

## The manual way

If you'd rather do it by hand (or the script can't reach Drive):

1. Download `epub_librarian-YYYY-MM-DD.db.gz` from Drive → `backups/`.
2. From `backend/`, with the backend stopped:
   ```
   mv epub_librarian.db epub_librarian.db.before-restore
   gunzip -c ~/Downloads/epub_librarian-2026-09-07.db.gz > epub_librarian.db
   rm -f epub_librarian.db-wal epub_librarian.db-shm
   ```
3. Restart the backend.

If the `.db.gz` won't open, use the SQL dump instead:

```
gunzip -c epub_librarian-2026-09-07.sql.gz | sqlite3 epub_librarian.db
```

## Notes

- Snapshots are taken with `VACUUM INTO`, so they're always internally
  consistent even though the app was running when they were made.
- A restore rewinds **everything** — books, corrections, review queue,
  operation history — to that date. There is no partial/selective restore.
