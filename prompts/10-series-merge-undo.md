# Task 10 — Series-merge moves are logged as undoable but "Undo" leaves a broken state (P2)

Read `prompts/README.md` first for shared context.

## Why

`series_merge_service.apply_series_merge` (`series_merge_service.py:257-323`)
writes one `Operation(action=move_and_rename, dry_run=False, status=done)` per
moved file, and deletes any source `Series` row (and its folder-emptying is
implied) once its books have moved.

`operation_service.undo_operation` (`operation_service.py:54-88`) treats **any**
completed non-dry-run `move`/`rename`/`move_and_rename` as undoable, and the
Activity page renders a per-row "Undo" button. Undoing a series-merge move:

- moves the Drive file back to `operation.original_parent_id` — which for a
  merged-away series whose folder became empty was **deleted** during the merge;
- sets `file_row.status = FileStatus.inbox` but does **not** restore
  `book.series` (still points at canonical) and does not recreate the deleted
  `Series` row;
- so the file ends up misfiled into a dead/re-created folder, marked `inbox`,
  and the next organize / nightly run just moves it back to canonical.

Prompt 01 required merge moves to be "undoable (SPEC §9)". They're logged, but
the undo doesn't actually undo.

## Goal

Make the behaviour honest. Recommended: **make series-merge operations
explicitly non-undoable** and give the user a real path instead.

1. Tag merge-origin operations so `undo_operation` rejects them —
   e.g. a distinct `OperationAction.series_merge` (needs an enum + migration —
   coordinate with task 08 if that's also in flight), or keep
   `move_and_rename` but set a `reason`/marker the undo guard checks. A new
   action value is cleaner.
2. `undo_operation` raises `OperationNotUndoableError` for them with a message
   that points at the real remedy ("re-run the merge with the other name as
   canonical, or split the series in Library Audit").
3. Activity page: hide/disable "Undo" for those rows, show the reason.
4. (Optional, only if cheap) a "reverse this merge" action that re-forks: create
   a fresh `Series` for the old name, repoint its books, move the files back.
   This is a real feature — don't half-build it; if it's not small, just do 1-3.

## Where it goes

- `backend/app/data/models.py` — `OperationAction` (if new value) + migration.
- `backend/app/services/series_merge_service.py` — tag the Operation rows.
- `backend/app/services/operation_service.py` — `undo_operation` guard + message;
  `_to_summary` / `OperationSummary` may need an `undoable: bool`.
- `frontend/src/pages/Activity.tsx` — Undo button gating.
- `backend/tests/test_operation_service.py`, `test_series_merge_service.py`.

## Acceptance criteria

- New test: applying a series merge then calling `undo_operation` on one of its
  Operation rows raises `OperationNotUndoableError` (or, if you built the real
  reverse, actually restores the split — series row recreated, files back,
  `book.series` restored).
- Existing undo tests for plain organize `move_and_rename` still pass.
- Activity page doesn't offer a broken Undo for merge rows.
- `alembic upgrade head` + `alembic check` clean if you added an enum value.
- `cd backend && pytest` green; `cd frontend && npm run build` green.
- Committed and pushed. ROADMAP + `project_bookbrain_ai_series_hallucination`
  memory updated.

## Gotchas

- If you add an `OperationAction` value, that's the same
  `sa.Enum` batch-migration dance as task 08 — do them together or sequence them.
- `list_operations` / the Activity page group by action label — a new action
  needs a display string.
- Don't retroactively rewrite existing `move_and_rename` rows from past merges in
  a data migration unless you can identify them unambiguously (the `reason`
  string `"series merge: 'X' -> 'Y'"` is your only signal — it's reliable, set at
  `series_merge_service.py:274`).
