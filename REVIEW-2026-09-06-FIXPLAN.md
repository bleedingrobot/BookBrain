# Fix plan — 2026-09-06 review batch #2

Companion to `REVIEW-2026-09-06.md` and `prompts/06`–`prompts/11`. This resolves
the open design choices in those prompts so the executing session
(`prompts/12-work-the-review-fixes.md`) doesn't have to re-derive them.

**Nothing here is done yet.** All three test suites were green at review time
(backend 353, viewer 60, frontend typecheck + lint).

---

## Execution order

One long session can do the whole batch; each stage ends in its own commit and
is independently shippable, so a fresh session can pick up at any stage boundary.

| Stage | Finding | Why here |
|-------|---------|----------|
| 1 | **06** — title-collision data loss (P0) | Highest severity; pure logic + tests, no migration. Do it first. |
| 2 | **08** — alembic enum drift (P2) | Establishes the SQLite enum-migration pattern and gets the tree `alembic check`-clean *before* stage 4 adds another enum value. |
| 3 | **07** — viewer sync drops files (P1) | Fully independent, viewer-only, separate deploy target. Slot it here as a clean break between backend chunks. |
| 4 | **10** — series-merge undo (P2) | Adds an `OperationAction` value → another enum migration, reusing stage 2's pattern. |
| 5 | **09** — organize write lock (P2) | Small; touches `organize_service` / `series_merge_service` which stage 4 also touched — do them adjacent. |
| 6 | **11** — AI spend guardrails (P2) | Independent polish, lowest urgency. |

Cross-cutting: stages 2 and 4 each add an Alembic migration for an `sa.Enum`
column on SQLite — same `batch_alter_table` recipe both times. Stage 2 should
leave a short comment in its migration that stage 4 can copy.

---

## Stage 1 — Finding 06: title-collision data loss

### Root cause

`text_match.normalize_title` strips everything from the first `:`/`;` onward
(`_SUBTITLE_SEPARATOR_RE`). `book_repository.resolve_book` uses it for the
*row-identity* decision (`book_repository.py:60-68`), so two same-author books
whose titles share a pre-colon prefix ("Mistborn: The Final Empire" /
"Mistborn: The Well of Ascension") resolve to one `Book` row. Then
`duplicate_service.detect_same_book_duplicates` groups by `book_id`, flags the
"extra" as `status=duplicate`/`same_book` (every scan + nightly, no user action),
which hides it from the viewer and the organizer — and `clear_duplicates` +
the bulk "Clear duplicates" button (Duplicates page and Dashboard) then trashes
the Drive file and hard-deletes its rows.

### Decisions

**1. New strict normalizer, used only for identity.**

Add `normalize_title_strict(text)` to `text_match.py`: identical to
`normalize_title` **minus** the `_SUBTITLE_SEPARATOR_RE` step. Keep the leading
-article strip and the trailing-parenthetical strip — those are safe for identity
(the distinguishing part of "Heir to the Empire (Thrawn Trilogy 1)" vs
"Dark Force Rising (Thrawn Trilogy 2)" is *before* the parens; only the `:` case
puts it *after*).

Use `normalize_title_strict` in:
- `book_repository.resolve_book` — the `target_title` / `b.canonical_title`
  comparison at lines 60-68. (This automatically covers `review_service.correct`
  and `sticky_resolution.resolve_corrected_book_id`, which both call
  `resolve_book`.)

Keep the existing loose `normalize_title` everywhere it's used for **scoring /
corroboration**, where a false match only moves a number:
- `confidence_service.score` (all its `normalize_title` calls)
- `identification_service` (`titles_match`, `_find_isbn_match`)
- `reident_audit_service` consensus checks
- `text_match.titles_match` itself stays loose (it's a scoring helper)

**Accepted trade-off:** "The Hobbit" and "The Hobbit: There and Back Again" now
resolve to two `Book` rows instead of one. That's a *lesser* harm — two visible
records for one book, no hiding, no trashing — than the current behaviour of
merging two genuinely different books. Document it in the `resolve_book`
docstring.

**2. Bulk-clear guard (defense in depth).**

Even with (1), a bad AI identification could still hand two different files an
identical title+author. So:
- `duplicate_service.clear_duplicates` — clear only rows whose `status_reason`
  is **not** `same_book` (i.e. exact-content `sha256` dups and
  `previously_rejected`). `same_book` rows are left alone.
- `list_duplicate_groups` / `Duplicates.tsx` — show `same_book` rows in a
  separate section with a **per-row** "Trash this copy" action (and a "Not a
  duplicate" action that clears the flag by re-pointing the file to a fresh
  book — reuse the `resolve_book` path). No bulk button for that section.
- New endpoint for the per-row trash, e.g.
  `POST /api/duplicates/{file_id}/clear` (single file, `status=duplicate` only).

**3. Fix the misleading header.** `Duplicates.tsx:37-42` says "detected by
content hash, not filename" — delete/replace that; it's wrong for `same_book`
and makes the bulk clear feel safer than it is.

**4. Repair existing casualties.** `run_rebuild` will **not** fix already-merged
books — it skips files already in `files` by `drive_file_id`. Add a one-off
repair, driven from the UI (James isn't terminal-savvy):
- `POST /api/library-audit/repair-title-merges` (+ button on the Library Audit
  page, or a section in the Re-identification tab). For every `Book` row with
  more than one `File`, re-derive each file's title from its own
  `metadata_sources` (`field_name='title'`, source `epub`/`comic`) or, failing
  that, its filename; if a file's `normalize_title_strict` differs from the
  primary's, split it onto its own `Book` (via `resolve_book`), set its status
  back to `organised` (it's in the library folder) or `inbox`, and clear a stale
  `same_book` flag. Report how many were split.
- If this piece balloons, ship 1–3 and file 4 as `prompts/13`.

### Files

`backend/app/services/text_match.py`, `book_repository.py`, `duplicate_service.py`,
`backend/app/api/routes/duplicates.py`, `backend/app/api/routes/library_audit.py`
(repair endpoint), `backend/app/schemas/duplicates.py`,
`frontend/src/pages/Duplicates.tsx`, `frontend/src/pages/LibraryAudit.tsx`,
`frontend/src/services/api.ts`. No migration.

### Done when

- New test: two same-author books with a shared pre-colon prefix → two `Book`
  rows, neither flagged `same_book`.
- New test: `review_service.correct`-ing one to a title sharing a prefix with
  another does **not** merge them.
- New test: `clear_duplicates` leaves `same_book` rows untouched; exact-content
  (`sha256`) clear is unchanged.
- Existing `confidence_service` / `identification_service` tests still pass
  (loose matching for scoring not regressed).
- Repair endpoint splits a seeded merged pair and reports it; makes zero AI/Drive
  calls in the split itself.
- `cd backend && pytest` + `cd frontend && npm run build` green.
- ROADMAP updated; `project_bookbrain_ai_series_hallucination` memory updated
  with the strict/loose split.

---

## Stage 2 — Finding 08: alembic enum drift + `alembic check` gate

### Root cause

`models.FileStatus` gained `rejected`; `models.FileStatusReason` gained
`previously_rejected`, `same_book`. No migration. `alembic check` fails. Harmless
on SQLite (no `CHECK` emitted) but a truncation risk on a length-enforcing DB
(`VARCHAR(14)` vs `previously_rejected` = 19 chars).

### Decisions

**1. One migration**, chained on head `a1b2c3d4e5f6`, bringing the DB column
definitions in line with the models:

```python
with op.batch_alter_table("files") as batch:
    batch.alter_column(
        "status",
        existing_type=sa.Enum("inbox","organised","review","unidentified","duplicate", name="filestatus"),
        type_=sa.Enum("inbox","organised","review","unidentified","duplicate","rejected", name="filestatus"),
        existing_nullable=False,
    )
    batch.alter_column(
        "status_reason",
        existing_type=sa.Enum("multi_parent","no_parent","manual_drift","parse_failed","low_confidence", name="filestatusreason"),
        type_=sa.Enum("multi_parent","no_parent","manual_drift","parse_failed","low_confidence","previously_rejected","same_book", name="filestatusreason"),
        existing_nullable=True,
    )
```

`batch_alter_table` on SQLite recreates `files` — **verify all three indexes
survive**: `ix_files_sha256`, `ix_files_sha256_status`, `ix_files_original_sha256`
(pass them explicitly via `recreate="always"` + `table_kwargs`/`copy_from` if
reflection drops them). `downgrade` restores the shorter member lists.

Leave a comment in the migration: *"SQLite Enum → VARCHAR, no CHECK; this
migration is a no-op on data and exists to satisfy `alembic check` and to record
the enum additions for a future length-enforcing DB. Same recipe as
`<stage-4 revision>` for `operations.action`."*

Keep the `Enum`s constraint-free — do **not** add `create_constraint=True`
(that would make every future enum addition a mandatory table rebuild).

**2. Wire the gate.** `backend/tests/test_migrations.py`:

```python
def test_no_migration_drift(tmp_path):
    db = tmp_path / "drift.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{db}"}
    subprocess.run(["alembic", "upgrade", "head"], cwd="…/backend", env=env, check=True)
    r = subprocess.run(["alembic", "check"], cwd="…/backend", env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
```

Subprocess (not the in-process API) because `alembic/env.py:18` unconditionally
overrides `sqlalchemy.url` from `get_settings().database_url`, and `get_settings`
is `@lru_cache` — a subprocess with `DATABASE_URL` set is the clean way to point
it at a temp DB. Resolve the `backend/` path relative to the test file.

### Files

`backend/alembic/versions/<new>.py`, `backend/tests/test_migrations.py`,
`prompts/README.md` (note `alembic check` is now part of "green").

### Done when

- `alembic upgrade head` then `alembic check` exits 0 on a fresh DB.
- `alembic downgrade -1` then `upgrade head` round-trips.
- The new test passes; `cd backend && pytest` green including the scan/duplicate
  suites (proving the `files` recreate kept its indexes/FKs).
- ROADMAP note.

---

## Stage 3 — Finding 07: viewer sync drops files on `parents`-less change

### Root cause

`librarySync.applyChanges` (`librarySync.ts:112-121`) evicts a cached file
whenever `(file.parents ?? []).some(p => folderIds.has(p))` is false — which is
true both when the file genuinely moved out *and* when the change record simply
has no `parents` field (Drive doesn't always send it).

### Decisions

In `applyChanges`, for a live (not removed, not trashed) non-folder change:
- `file.parents === undefined` → **skip**: leave any existing cache entry as-is,
  don't add a never-seen file.
- `file.parents` is `[]` or a populated array → existing `parentKnown` logic
  (a genuinely parent-less file isn't in our tree; a populated array with no
  known parent means moved out → evict).

Apply the same guard to the folder-removal pass (`librarySync.ts:105-110`): a
folder change with `parents === undefined` must not evict a folder already in
`folderIds`.

`DriveChange.file.parents` is already typed `string[] | undefined` (`drive.ts`),
so `undefined` vs `[]` is distinguishable without a type change.

### Files

`library-viewer/src/lib/librarySync.ts`; new `library-viewer/src/lib/librarySync.test.ts`.

### Done when

- New tests: (a) cached file + change with no `parents` key → still cached;
  (b) change with `parents:['unknown-folder']` → evicted (unchanged);
  (c) `removed:true` / `trashed:true` → evicted (unchanged); (d) known folder +
  `parents`-less change → not evicted.
- The existing nested-new-folder multi-pass resolution still works.
- `cd library-viewer && npm run build && npx vitest run && npm run lint` green.
- Commit **and push** (this deploys the viewer via the Pages workflow — build +
  tests must be green first). ROADMAP + memory note on the librarySync drift class.

---

## Stage 4 — Finding 10: series-merge "Undo" leaves a broken state

### Root cause

`series_merge_service.apply_series_merge` logs each moved file as
`Operation(action=move_and_rename, dry_run=False, status=done)` and deletes the
emptied source `Series` + folder. `operation_service.undo_operation` treats any
completed non-dry-run `move_and_rename` as undoable and
`frontend/src/pages/Activity.tsx:58-64` shows an Undo button for it — but undo
moves the file back to a **deleted** folder, doesn't restore `book.series`, and
doesn't recreate the `Series` row.

### Decisions

**Make merge operations honestly non-undoable** (don't build a real reverse —
that's a feature, not a fix; file it separately if wanted).

1. `models.OperationAction` — add `series_merge = "series_merge"`. Migration
   (`batch_alter_table("operations")` on `action`, same recipe as stage 2) that
   also converts existing rows:
   `UPDATE operations SET action='series_merge' WHERE action='move_and_rename'
   AND reason LIKE 'series merge:%'` — the `reason` prefix is set reliably at
   `series_merge_service.py:274`.
2. `series_merge_service.apply_series_merge` — write
   `action=OperationAction.series_merge`.
3. `operation_service.undo_operation` — `series_merge` is not in
   `_UNDOABLE_ACTIONS`, so it already raises `OperationNotUndoableError`; make
   the message useful ("a series merge can't be auto-undone — re-run the merge
   with the other name as canonical, or split it in Library Audit").
4. `OperationSummary` — add `undoable: bool` (computed:
   `action in _UNDOABLE_ACTIONS and not dry_run and status == done`). Export
   `_UNDOABLE_ACTIONS` or a helper.
5. `Activity.tsx` — gate the Undo button on `op.undoable`; show the action label
   for `series_merge`; this also correctly hides Undo for `write_metadata` rows
   (which currently show a button that just errors on click).

### Files

`backend/app/data/models.py`, `backend/alembic/versions/<new>.py`,
`backend/app/services/series_merge_service.py`, `operation_service.py`,
`backend/app/schemas/operations.py`, `frontend/src/pages/Activity.tsx`,
`frontend/src/types/operations.ts`, tests
(`test_operation_service.py`, `test_series_merge_service.py`).

### Done when

- New test: apply a merge, then `undo_operation` on one of its rows →
  `OperationNotUndoableError`.
- Existing undo tests for plain organize `move_and_rename` still pass.
- `alembic upgrade head` + `alembic check` clean; downgrade round-trips; the
  data-conversion `UPDATE` is covered (seed a pre-migration `move_and_rename`
  row with the `series merge:` reason, upgrade, assert it became `series_merge`).
- Activity page offers no Undo for merge or `write_metadata` rows.
- `cd backend && pytest` + `cd frontend && npm run build` green.
- ROADMAP + `project_bookbrain_ai_series_hallucination` memory updated.

---

## Stage 5 — Finding 09: organize write lock

### Root cause

`conftest._reset_shared_singletons` resets five module-level locks/caches but not
`OrganizeService._write_lock` (an instance attr on the module singleton
`_organize_service`). Latent "Lock bound to a different event loop" the moment a
second test exercises the organize singleton. Also: three uncoordinated
commit-serialisation locks (`book_repository._book_write_lock`,
`OrganizeService._write_lock`, `series_merge_service._write_lock`).

### Decisions

**Go with the unify option (review's Option B); keep the minimal reset as the
fallback.**

1. First, unconditionally safe: add `reset_organize_write_lock()` to
   `organize_service.py` and call it from `conftest._reset_shared_singletons`;
   fix that fixture's "complete list" docstring claim. Commit-worthy on its own.
2. Then attempt the unify: `OrganizeService._organize_file` and
   `series_merge_service.apply_series_merge` take
   `book_repository.get_book_write_lock()` around their `session.commit()`
   instead of a private lock. Delete `OrganizeService._write_lock`,
   `series_merge_service._write_lock` and their `reset_*` helpers (and the
   conftest lines for them). Keep `nightly._nightly_lock` — it's a
   "run already active" guard, a different concern.
   - **Keep the shape:** `apply_series_merge` must still do its Drive
     `move_and_rename` calls *outside* any lock; only the final commit takes it.
   - Keep B only if every concurrency test
     (`test_process_files_concurrently_*`, the organize/merge suites) stays green
     with no throughput regression. If anything's marginal, ship step 1 alone and
     note B as a follow-up.

### Files

`backend/app/services/organize_service.py`, `series_merge_service.py`,
`backend/tests/conftest.py`, tests.

### Done when

- Two separate test functions each running scan→auto-organize (or two
  `get_organize_service().organize_eligible_files(...)` calls) both pass.
- If B landed: `grep -rn "_write_lock" backend/app/services/` shows only the
  shared one plus `nightly`'s.
- `cd backend && pytest` green including the concurrency tests.
- ROADMAP note.

---

## Stage 6 — Finding 11: AI spend guardrails

### Root cause

`POST /api/library/descriptions?ai=true` → `backfill_descriptions(use_ai=True,
limit=None)` fans out ~950 unbounded `describe()` calls, one checkbox + click,
tooltip only. `POST /api/library/rebuild` re-identifies every unknown file (~2200
after a Clear Library) with no warning. Contrast `reident_audit_service`: cap 50
+ `$` estimate first.

### Decisions

**Descriptions:**
1. `config.py` — `ai_description_cap: int = 200`,
   `ai_description_cost_usd: float = 0.01` (smaller call than the deep-check's
   `_DEEP_CHECK_USD_PER_ROW = 0.02` — ~150 in + ~400 out).
2. `description_service.backfill_descriptions` — on the `use_ai` path, stop
   making AI calls after `cap` in a run (the free provider pass stays uncapped).
   `_books_needing_descriptions` is stateless, so a re-run continues where it
   left off. Reflect the cap in the `remaining` count.
3. `GET /api/library/descriptions/estimate` →
   `{books_needing_ai, will_process, cap, estimated_cost_usd}`. **Zero AI
   calls** — it only runs the free provider/OL lookups? No — cheaper: it counts
   `_books_needing_descriptions` and assumes all still need AI (upper bound).
   Keep it a pure DB count + arithmetic.
4. `Library.tsx` — when the AI box is ticked, fetch the estimate and show
   "~N books, ~$X, runs in batches of {cap}"; require a second explicit click,
   same UX as `ReidentAuditPanel`'s deep-check confirm.

**Rebuild:**
5. `GET /api/library/rebuild/estimate` → count of `.epub`s in the library tree
   not already in `files` (one `provider.list_epub_files_recursive` +
   set-difference against known `drive_file_id`s), × `identify` cost estimate.
   If no library folder / creds, return a static "re-identifies every unknown
   book" note. Don't block rebuild if the estimate call fails — degrade to
   "couldn't estimate, proceed?".
6. Rebuild button / confirm dialog shows "~N books, ~$X" before kicking off.

### Files

`backend/app/core/config.py`, `backend/app/services/description_service.py`,
`backend/app/services/scan_service.py` (or a small `rebuild_estimate` helper),
`backend/app/api/routes/library.py`, `backend/app/schemas/` (estimate models),
`frontend/src/pages/Library.tsx`, `frontend/src/services/api.ts`, tests.

### Done when

- `ai=true` backfill processes ≤ `cap` books per run; `remaining` reflects it;
  a re-run continues.
- Estimate endpoints return sane numbers and make **zero** Anthropic calls
  (test with a mock that fails on call).
- Frontend shows the estimate + a confirm before any AI description run and
  before rebuild.
- `cd backend && pytest` + `cd frontend && npm run build` green.
- ROADMAP note.

---

## Batch definition of done

- All six stages committed (stage 3 also pushed; the rest pushed too once green —
  `backend`/`frontend` don't deploy but still commit + push per `prompts/README.md`).
- `cd backend && pytest` · `cd frontend && npm run build && npm run lint` ·
  `cd library-viewer && npm run build && npx vitest run && npm run lint` — all green.
- `cd backend && alembic upgrade head && alembic check` — clean.
- `REVIEW-2026-09-06.md` findings table annotated with commit SHAs (or a short
  "shipped" section appended).
- `prompts/README.md` batch-#2 table marked shipped.
- Memory: `project_bookbrain_review_followups` updated to "batch #2 shipped";
  `project_bookbrain_ai_series_hallucination` updated with the 06 fix.
- James told: (a) restart the backend (his running one is on pre-change code),
  (b) run the title-merge repair once and review what it split, (c) the two
  stale `uvicorn :8000` processes from the review are still worth killing.
