# Task 12 — Work through the 2026-09-06 review batch #2 fixes

Read `prompts/README.md` first for shared context (repo layout, three apps,
test/deploy conventions, James isn't terminal-savvy, Windows uvicorn gotchas).

This is a **multi-stage, multi-commit** task. Work the stages **in order**. Each
stage ends in its own commit and is independently shippable — if you run low on
context, stop after any committed stage and a fresh session can resume at the
next one.

## Inputs — read all of these before starting

- `REVIEW-2026-09-06.md` — the review. Findings, evidence, `file:line`.
- `REVIEW-2026-09-06-FIXPLAN.md` — **the plan. Every open design choice in the
  per-finding prompts below is already resolved here. Follow it.**
- `prompts/06-title-collision-false-duplicates.md`
- `prompts/07-librarysync-missing-parents.md`
- `prompts/08-alembic-enum-drift.md`
- `prompts/09-organize-write-lock.md`
- `prompts/10-series-merge-undo.md`
- `prompts/11-ai-spend-guardrails.md`

Where a per-finding prompt and the FIXPLAN differ in detail, **the FIXPLAN
wins** (it was written second, with the choices made).

## Before you touch anything

1. `git status` — expect a clean tree on `main` (the review left
   `REVIEW-2026-09-06*.md` and `prompts/06`–`prompts/12` — those may already be
   committed; if not, that's fine, leave them).
2. Check for orphaned `uvicorn` workers holding `epub_librarian.db`
   (`Get-CimInstance Win32_Process -Filter "name='python.exe'"`). The review
   noted two stragglers on `:8000`. Kill stale ones before running anything that
   opens the DB, so a lock error means a real bug.
3. Baseline the suites so you know you started green:
   - `cd backend && pytest -q`
   - `cd frontend && npm run build && npm run lint`
   - `cd library-viewer && npm run build && npx vitest run && npm run lint`

## Stages

Do these in this order (rationale in the FIXPLAN "Execution order" table):

### Stage 1 — Finding 06 (P0): title-collision data loss

FIXPLAN §"Stage 1". Pure logic + tests, no migration.

- `normalize_title_strict` in `text_match.py` (= `normalize_title` minus the
  `:`/`;` subtitle strip; keep leading-article + trailing-paren strips).
- Use it in `resolve_book`'s row-identity match **only**. Leave loose
  `normalize_title` in every scoring/corroboration call site (list is in the
  plan).
- `clear_duplicates` skips `same_book` rows; add a per-row clear endpoint + UI
  section for `same_book`; fix the misleading Duplicates page header.
- Repair pass for existing casualties: `POST /api/library-audit/repair-title-merges`
  + a button. If this piece gets large, ship the matcher + guard and file the
  repair as `prompts/13`.

**Commit** (`fix: strict title match so distinct books stop merging into false
duplicates`). Then `cd frontend && npm run build`, `cd backend && pytest`.

### Stage 2 — Finding 08 (P2): alembic enum drift + `alembic check` gate

FIXPLAN §"Stage 2".

- One `batch_alter_table("files")` migration on head `a1b2c3d4e5f6` bringing
  `status` / `status_reason` enums in line with the models. **Verify
  `ix_files_sha256`, `ix_files_sha256_status`, `ix_files_original_sha256` survive
  the SQLite table recreate.** Leave the "same recipe as stage 4" comment.
- `backend/tests/test_migrations.py` running `alembic upgrade head` + `alembic check`
  in a subprocess with `DATABASE_URL` pointed at a temp DB (env.py overrides the
  url from settings — subprocess is the clean way).

**Commit** (`fix: migrate status/status_reason enum additions; gate alembic check
in tests`).

### Stage 3 — Finding 07 (P1): viewer sync drops files on `parents`-less change

FIXPLAN §"Stage 3". `library-viewer` only.

- In `applyChanges`, distinguish `file.parents === undefined` (skip, leave cache
  entry) from `[]` / populated (existing evict logic). Same for the folder pass.
- New `library-viewer/src/lib/librarySync.test.ts`.

**Commit AND push** — this deploys the viewer via the Pages workflow. Build +
`vitest` + lint must be green first.

### Stage 4 — Finding 10 (P2): series-merge undo

FIXPLAN §"Stage 4".

- `OperationAction.series_merge`; migration (batch alter `operations.action`,
  same recipe as stage 2) **including** the data-conversion `UPDATE` for existing
  `move_and_rename` rows with a `reason LIKE 'series merge:%'`.
- `apply_series_merge` writes the new action; `undo_operation` message; add
  `undoable: bool` to `OperationSummary`; gate the Activity Undo button on it
  (this also hides the broken Undo on `write_metadata` rows).

**Commit** (`fix: series-merge operations are not auto-undoable`).

### Stage 5 — Finding 09 (P2): organize write lock

FIXPLAN §"Stage 5".

- Guaranteed: `reset_organize_write_lock()` + conftest call + fix the fixture
  docstring's "complete list" claim.
- Then attempt the unify (route organize + series-merge commits through
  `get_book_write_lock()`, delete the two private locks). Keep only if all
  concurrency tests stay green with no throughput regression; otherwise ship just
  the reset and note the unify as a follow-up.

**Commit** (`fix: reset the organize write lock between tests` — extend the
message if the unify landed).

### Stage 6 — Finding 11 (P2): AI spend guardrails

FIXPLAN §"Stage 6".

- `ai_description_cap` (default 200) + a per-description cost constant in config;
  cap the `use_ai` path; `GET /api/library/descriptions/estimate` (pure DB count,
  zero AI calls).
- `GET /api/library/rebuild/estimate` (Drive listing minus known files; degrade
  gracefully); show "~N books, ~$X" on both confirms in `Library.tsx`.

**Commit** (`feat: cap + cost estimate for AI descriptions and rebuild`).

## Finish

1. Full green sweep — all three suites **plus**
   `cd backend && alembic upgrade head && alembic check`.
2. Append a short "## Shipped 2026-…" section to `REVIEW-2026-09-06.md` with the
   commit SHA per finding; mark the `prompts/README.md` batch-#2 table shipped.
3. `git push` (backend/frontend don't deploy but still push per conventions;
   stage 3 already pushed).
4. Update memory: `project_bookbrain_review_followups` → "batch #2 shipped";
   `project_bookbrain_ai_series_hallucination` → the 06 strict/loose split.
5. Report to James, in plain language:
   - what changed and what he needs to do: **restart the backend** (his running
     one is on old code), **run the title-merge repair once** and eyeball what it
     split, and the two stale `uvicorn :8000` processes are still worth killing;
   - anything you shipped partial (e.g. repair filed as `prompts/13`, unify
     deferred) and why.

## Gotchas (batch-wide)

- **Two Enum migrations** (stages 2 and 4), both `batch_alter_table` on SQLite,
  both recreate their table — re-run the full `test_scan_service` /
  `test_duplicate_service` / `test_operation_service` suites after each, not just
  `alembic check`. Don't add `create_constraint=True` to any `Enum`.
- **Don't run anything that hits real Drive or the real Anthropic API.** The
  test suites use mocks + in-memory DB. `alembic` migrations run against a temp
  or the local dev DB only.
- **`resolve_book` is reached from five paths** (scan fast-path, `find_rule_match`,
  AI path, `review_service.correct`, `sticky_resolution`). The stage-1 change is
  one function but affects all of them — lean on the existing tests for each.
- **Existing merged books** in James's real DB won't un-merge on their own;
  `run_rebuild` skips known files. That's what the stage-1 repair endpoint is
  for. Say so in the report.
- Stage 5's unify: `apply_series_merge` must keep doing its Drive moves
  **outside** the lock — only the commit takes it. Don't hold a DB lock across
  `asyncio.to_thread(provider.move_and_rename, …)`.
- Keep `nightly._nightly_lock` alone — it's a "run already active" guard, not a
  commit serialiser.
