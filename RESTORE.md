# Restoring the database from a backup

BookBrain drops a dated snapshot of `backend/epub_librarian.db` into a
`backups/` folder inside your Drive **library folder** on every nightly run
(and whenever you click **Back up now** in Settings). The last 7 are kept.

Each backup is two files:

| File | What it is |
|------|-----------|
| `epub_librarian-YYYY-MM-DD.db.gz` | gzipped SQLite database — an exact copy, restore this |
| `epub_librarian-YYYY-MM-DD.sql.gz` | gzipped SQL text dump — a portable fallback if the binary won't open |

## Restore

1. **Stop the backend** (close the `uvicorn` window / kill any stray
   `uvicorn --reload` workers — nothing may hold the DB file open).
2. Download the `.db.gz` you want from Drive → `backups/`.
3. From `backend/`:
   ```
   # keep the current one just in case
   mv epub_librarian.db epub_librarian.db.before-restore

   gunzip -c ~/Downloads/epub_librarian-2026-09-07.db.gz > epub_librarian.db
   rm -f epub_librarian.db-wal epub_librarian.db-shm
   ```
4. Restart the backend. Check the Library page loads and the book count looks
   right.

## If the `.db.gz` won't open

Use the SQL dump instead:

```
gunzip -c epub_librarian-2026-09-07.sql.gz | sqlite3 epub_librarian.db
```

## Notes

- The snapshots are taken with `VACUUM INTO`, so they're always internally
  consistent even though the app was running when they were made.
- A restore rewinds **everything** — books, corrections, review queue,
  operation history — to that date. There's no partial/selective restore.
- The Google OAuth token lives in the DB too, so after a restore you may need
  to reconnect Google in Settings if the token has since been refreshed.
