# Task 08 — `files.status` / `files.status_reason` enum drift; `alembic check` is red (P2)

Read `prompts/README.md` first for shared context.

## Why

`cd backend && alembic check` against a fresh `alembic upgrade head` database
**fails**:

```
Detected type change from VARCHAR(length=14) to
Enum('multi_parent','no_parent','manual_drift','parse_failed','low_confidence',
     'previously_rejected','same_book', name='filestatusreason') on 'files.status_reason'
FAILED: New upgrade operations detected
```

Since the init migration (`alembic/versions/a4b6cf91c1a2_init_schema.py:69-70`):

- `models.FileStatus` gained `rejected` (used by `review_service.reject` and
  checked in `scan_service._insert_duplicate_file`);
- `models.FileStatusReason` gained `previously_rejected` and `same_book`.

No migration records any of this. On SQLite it's currently harmless —
SQLAlchemy's `Enum` emits no `CHECK` constraint by default, so the columns are
bare `VARCHAR` and the new values persist fine (the full test suite is green).
But:

- there's no schema-drift gate — `alembic check` isn't run anywhere;
- the SQLite column is `VARCHAR(14)` and `previously_rejected` is 19 chars, so a
  future length-enforcing DB (SPEC §9 names Postgres) truncates it under a
  hand-written or naïve migration.

## Goal

1. A migration that brings the `filestatus` and `filestatusreason` column
   definitions in the DB in line with the current models.
   - SQLite can't `ALTER` a column type in place — use
     `op.batch_alter_table('files')` with `batch_op.alter_column('status', ...)`
     / `alter_column('status_reason', ...)` to recreate the table with the
     correct `sa.Enum(...)` definitions. Verify `ix_files_sha256_status` and
     `ix_files_sha256` (from the writeback migration) survive the batch recreate.
   - Downgrade should restore the previous enum member lists.
2. Wire `alembic check` into the test workflow so this can't silently reappear —
   a line in the backend test command / a tiny `test_migrations_in_sync` test
   that shells out to `alembic check` and asserts exit 0, or a CI step.

## Where it goes

- `backend/alembic/versions/` — new migration, chained onto the current head
  (`a1b2c3d4e5f6`). Confirm with `alembic heads` / `alembic history`.
- `backend/tests/` — a `test_migrations_in_sync.py` (or extend an existing infra
  test) if you go the test route.
- `README.md` / `prompts/README.md` — note that `alembic check` is now part of
  "green".

## Acceptance criteria

- `alembic upgrade head` then `alembic check` exits 0 on a fresh DB.
- `alembic downgrade -1` then `alembic upgrade head` round-trips cleanly.
- `cd backend && pytest` green — including the new sync check.
- No data migration needed (values already stored are valid members); confirm
  by upgrading a DB that already has `rejected` / `same_book` rows seeded.
- Committed and pushed. ROADMAP note.

## Gotchas

- `batch_alter_table` on SQLite recreates the table — every index, FK and the
  `Index("ix_files_sha256_status", ...)` / `original_sha256` index must be
  reasserted. Run the full `test_scan_service` / `test_duplicate_service` suites
  after, not just `alembic check`.
- Don't add `create_constraint=True` to the `Enum`s — that would put a hard
  `CHECK` on SQLite and make every future enum addition a mandatory migration
  with a table rebuild. Keep them constraint-free; the migration is just about
  making `alembic check` honest and keeping the column wide enough.
- If `alembic check` needs a live DB URL, point it at a temp file DB in the test
  (`DATABASE_URL=sqlite+aiosqlite:///./_check.db`) and clean up.
