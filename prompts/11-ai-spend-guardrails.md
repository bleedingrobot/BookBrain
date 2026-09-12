# Task 11 — Cap + cost estimate for `descriptions?ai=true` and rebuild (P2)

Read `prompts/README.md` first for shared context.

## Why

Two "click once, spend tens of dollars, no warning" paths:

1. **`POST /api/library/descriptions?ai=true`**
   (`api/routes/library.py:120-131` → `description_service.backfill_descriptions(
   use_ai=True, limit=None)`). Fans out one `anthropic_client.describe()` call
   per organised book still lacking a description — ROADMAP puts that at ~950 —
   with **no cap and no pre-flight estimate**. The frontend
   (`Library.tsx:280-295`) is one checkbox + one button; the only guard rail is a
   tooltip. Contrast `reident_audit_service`: deep-check caps at
   `DEEP_CHECK_CAP = 50`, has an `estimate_deep_check` endpoint, and shows
   `estimated_cost_usd` before the user commits.

2. **`POST /api/library/rebuild`** (`api/routes/library.py:33-51` →
   `scan_service.run_rebuild`). Re-runs the full identification pipeline (AI
   included) for every file not already in `files`. After a "Clear Library"
   that's ~2200 identify calls, unattended once triggered, with nothing telling
   the user.

Neither is a runaway — both are bounded by real library size and user-initiated
— but the user has no way to see the cost coming.

## Goal

- **Descriptions:** add a per-run cap (config value, default e.g. 200) to the
  `ai=true` path, and an estimate endpoint mirroring
  `reident_audit_service.estimate_deep_check` —
  `GET /api/library/descriptions/estimate` returning
  `{ books_missing, will_process, cap, estimated_cost_usd }`. Frontend: when the
  AI box is ticked, show "~N books, ~$X — runs in batches of {cap}" and require a
  second click, same UX shape as the deep-check.
- **Rebuild:** the rebuild confirm dialog (or a new
  `GET /api/library/rebuild/estimate`) should show how many files would be
  (re-)identified and a rough `$` figure, so "recover after Clear Library" isn't
  a blind spend. No cap needed — rebuild genuinely has to do them all — just
  visibility.
- Reuse `reident_audit_service._DEEP_CHECK_USD_PER_ROW` / factor out a shared
  per-call cost constant so there's one place to update pricing. `describe()` is
  a smaller call than `audit_book_identity` (~150 in + ~400 out) — use a
  separate, smaller constant, not the deep-check one.

## Where it goes

- `backend/app/core/config.py` — `description_ai_cap` (or similar).
- `backend/app/services/description_service.py` — respect the cap on the
  `use_ai` path; an `estimate_description_backfill()` helper.
- `backend/app/services/scan_service.py` or a small helper — count files a
  rebuild would identify (files in the library tree not in `files`).
- `backend/app/api/routes/library.py` — estimate endpoints.
- `backend/app/schemas/` — estimate response models.
- `frontend/src/pages/Library.tsx` — descriptions confirm + estimate display.
- Wherever the rebuild button lives (`Library.tsx` / a Settings page) — rebuild
  estimate + confirm.

## Acceptance criteria

- `ai=true` description backfill processes at most `cap` books per run and
  reports `remaining` (it already has a `remaining` field — make it reflect the
  cap, not just `limit`).
- Estimate endpoint returns a sane count + non-zero cost for a seeded library
  with N description-less books; test asserts it makes **zero** Anthropic calls.
- Frontend shows the estimate before the AI run and requires an explicit
  confirm.
- Rebuild confirm shows a file count (+ rough cost) before kicking off.
- `cd backend && pytest` green; `cd frontend && npm run build` green.
- Committed and pushed. ROADMAP note.

## Gotchas

- The free (non-AI) description pass should stay uncapped and one-click — it
  costs nothing. Only gate the `ai=true` path.
- `_books_needing_descriptions` already excludes books that got a description, so
  re-running after a capped batch naturally continues where it left off — verify
  that's true with the cap in place (it should be; the query is stateless).
- Don't block the rebuild on the estimate call failing — degrade to "couldn't
  estimate, proceed?" rather than making Clear-Library recovery unreachable.
- Pricing constants are guesses; label them clearly as padded estimates like
  `reident_audit_service` already does (`~1.5k in + ~0.4k out per call`).
