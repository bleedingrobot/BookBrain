# Task 1 — Ship the series-merge / library-audit work

Read `prompts/README.md` first for shared context.

## Background

A previous session built a **series-merge** feature on top of the existing
Library Audit page and left it **uncommitted** in the working tree. Library
Audit already detects clusters of author/series DB rows that look like the same
thing forked into two (the AI phrased a series differently on two books, so
`organize_service` made two Drive folders). This work adds: dismissing clusters,
and *investigating + applying* a series merge — collapsing the duplicate `Series`
rows into one, adding a `SeriesAlias`, and moving the affected Drive folders/files
to the canonical path.

It targets the recurring "AI series hallucination" pain (see memory) — the
manual fix today is per-book `/correct` + re-Organize.

## What's on disk (uncommitted as of 2026-09-06)

Modified:
- `backend/app/api/routes/library_audit.py` — new endpoints: `POST /dismiss`,
  `GET /dismissed`, `POST /dismissed/{id}/restore`, `POST /series/investigate`,
  `POST /series/apply`
- `backend/app/data/models.py` — `DismissedAuditCluster`, `AuditClusterKind`
- `backend/app/providers/ai/{anthropic_client,schema,types}.py` — an AI call to
  judge whether two series names are really the same series
- `backend/app/providers/drive/provider.py` — folder move/rename helpers
- `backend/app/schemas/library_audit.py`
- `backend/app/services/library_audit_service.py` — dismiss/undismiss/list
- `backend/tests/{conftest.py,test_drive_provider.py,test_library_audit_service.py}`
- `frontend/src/pages/LibraryAudit.tsx`, `frontend/src/services/api.ts`,
  `frontend/src/types/libraryAudit.ts`, `frontend/vite.config.ts`

Untracked (new):
- `backend/alembic/versions/13df754eacc9_add_dismissed_audit_clusters.py`
- `backend/app/schemas/series_merge.py`
- `backend/app/services/series_merge_service.py` (has a module-level `_write_lock`
  + `reset_series_merge_write_lock()` — check `conftest.py` resets it)
- `backend/tests/test_series_merge_service.py`
- `frontend/src/components/SeriesMergePanel.tsx`
- `frontend/src/types/seriesMerge.ts`

## Your job

This is a **review-and-ship** task, not a build-from-scratch. Do NOT rewrite it —
land it, fixing only what's actually broken.

1. Read every changed/new file. Understand the full flow: cluster → investigate
   (AI opinion + proposed canonical name + planned Drive moves) → apply.
2. Check the wiring:
   - Is `series_merge_service` reachable? Is the migration chained correctly onto
     the current Alembic head (`cd backend && alembic heads` / `alembic history`)?
   - `SeriesMergePanel` actually rendered by `LibraryAudit.tsx`?
   - Any new module-level lock reset in `conftest.py`?
   - `frontend/vite.config.ts` change — understand why it changed, make sure it's
     intentional and not debris.
3. Run the full suites:
   - `cd backend && pytest` — everything green, including the two new test files.
   - `cd frontend && npm run build` — typechecks.
   - `cd library-viewer && npm run build && npx vitest run && npm run lint` — the
     viewer isn't touched, but confirm nothing bled across.
4. Exercise it against the real running app if servers are up (check first for
   orphaned uvicorn workers). At minimum: load Library Audit, confirm the page
   renders, dismiss/restore a cluster. Only *apply* a merge if James okays a real
   Drive change — otherwise stop at "investigate" and report what it proposed.
5. Fix anything genuinely broken. If a piece is half-built and risky, it's better
   to comment it out / feature-flag it and ship the safe part than to block the
   whole thing — but say so clearly in your report.
6. Commit in **logical chunks** (backend service + migration + tests; then API;
   then frontend) or one commit if it's cleaner. Push to `main`.
7. Update `ROADMAP.md` (move series-merge to Done) and `SPEC.md` if the feature
   set materially changed. Update the memory note
   `project_bookbrain_ai_series_hallucination` with how the merge flow works now.

## Acceptance criteria

- `pytest` and both `npm run build`s pass.
- Library Audit page loads and the dismiss/restore + investigate paths work end
  to end against the running backend.
- The Alembic migration applies cleanly on top of `alembic upgrade head` from the
  committed state, and `alembic downgrade` is defined.
- No stray debug code, no half-wired imports.
- Committed and pushed; ROADMAP updated.

## Gotchas

- `series_merge_service` moves **real Drive folders**. The apply path must be
  idempotent and must write `operations` rows so it's undoable (SPEC §9). Verify
  this — if it doesn't, that's a real bug to fix before shipping.
- Don't touch anything under `library-viewer/` — separate concern.
- If the AI "same series?" call is on the *apply* path (not just advisory on
  investigate), make sure a failed/timed-out call degrades gracefully rather than
  aborting a half-done folder move.
