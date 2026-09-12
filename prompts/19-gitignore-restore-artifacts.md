# Task 19 — .gitignore the backup/restore artifacts (REVIEW-2026-09-08 F7)

Trivial. Do it first.

## Why

`app/jobs/restore.py` renames the live DB to
`backend/epub_librarian.db.pre-restore-<timestamp>` before a restore, and the
manual `RESTORE.md` flow suggests `epub_librarian.db.before-restore`. Neither
matches `.gitignore`'s `backend/*.db` (they don't end in `.db`) or
`backend/*.db.bak-*`. A `.pre-restore` file is a full database copy including
the encrypted OAuth token — it must never be committable.

`app/jobs/backup_job.py` writes `backend/backup-runs.log` — `.gitignore`
covers `backend/nightly-runs.log*` but not this one.

## Goal

Add to the repo-root `.gitignore`, under "Backend runtime":

```
backend/backup-runs.log*
backend/*.db.pre-restore-*
backend/*.db.before-restore
```

Confirm with `git check-ignore` that a sample path of each form is now ignored,
and that `git status` is still clean (nothing already tracked matches).

## Acceptance

- `git check-ignore backend/epub_librarian.db.pre-restore-20260908T120000`
  prints the path.
- `git check-ignore backend/backup-runs.log` prints the path.
- `git status --porcelain` unchanged.

One commit.
