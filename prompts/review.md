# BookBrain — full project review

Run this in its **own fresh Claude Code session**. It produces a written
assessment plus a set of self-contained work-prompt files — it does **not**
change any code. Re-run it whenever you want a fresh pass (every few weeks, or
after a burst of feature work).

## Your job

A rigorous, **read-only** review of the whole project — backend, frontend, and
library-viewer. Deliverables:

1. A written review (strengths, a system map, findings by severity, each with
   `file:line` evidence and a concrete fix).
2. One self-contained work-prompt file per P0–P2 finding worth acting on, in
   `prompts/`, each runnable in a fresh session.

Do not fix anything, migrate, commit, or deploy during the review.

## Orientation — read these first, in order

- `README.md`, `SPEC.md`, `ROADMAP.md` (the "Done" section is long and is the
  authoritative record of deliberate deviations from SPEC).
- `prompts/README.md` and every existing `prompts/*.md`. The last review's
  output shipped as prompts 01/02/03/05 — check ROADMAP's "Done" entries and
  `git log` to see what's already landed so you don't re-file it.
- The three apps:
  - `backend/` — FastAPI + SQLAlchemy 2.0 async + Alembic, SQLite. Strict
    layering `api/ → services/ → providers/ → data/`. `cd backend && pytest`.
    Dev server `uvicorn app.main:app --reload` on `:8000`.
  - `frontend/` — React + TS + Vite + TanStack Query + Tailwind v4, the local
    admin UI. `cd frontend && npm run build` typechecks; `npm run lint`.
  - `library-viewer/` — separate static React app, the family-facing browser.
    Auto-deploys to GitHub Pages via a workflow on push to `main`.
    `cd library-viewer && npm run build && npx vitest run && npm run lint`.

## Ground rules

- **Read-only.** No edits, no `alembic upgrade`, no commits, no pushes.
- **Never spend money or touch real data.** Do not run anything that calls the
  Anthropic API, calls Google Drive with a real token, or moves/organizes real
  files. The live library is ~2200 real books in a real Drive. The test suites
  use mocks + an in-memory DB — those are safe and expected.
- **Windows gotcha:** orphaned `uvicorn --reload` workers hold the SQLite file
  open → "database is locked". Check for and account for stragglers before
  attributing a lock error to code.
- **The user (James) is not terminal-savvy.** Run every command yourself; never
  hand him a list to type. He evaluates against a running app, not diffs.
- `anthropic_model = claude-opus-5`. App-computed confidence is authoritative
  (SPEC §1) — flag any path that routes on the AI's self-reported confidence.

## What to actually do

1. **Map the system.** The `api/services/providers/data` layering and any
   violations of it; the data model (`app/data/models.py`) and every migration;
   the identification pipeline (`scan_service` → `candidate_service` →
   `identification_service` → `confidence_service` → `book_repository`); the
   caching/sidecar layers (`ai_decisions`, `bookbrain-index.json`,
   `bookbrain-viewer-settings.json`, `bookbrain-activity-log.json`,
   `bookbrain-wishlist.json`, the reident report blob); the job/scheduler setup
   (`app/jobs/`, `job_runs`, the in-memory job trackers).

2. **Run all three test suites.** Report results honestly — name every failure,
   flake, and `skip`. Then assess coverage informally: which services, routes,
   and error paths have **no** test? `book_repository`, `organize_service`,
   `sticky_resolution`, the reident deep-check, the Drive change-feed sync in
   the viewer (`librarySync.ts`) are worth special attention.

3. **`git log` archaeology.** Find the files and subsystems that have been
   fixed *repeatedly* — the recurring threads are "concurrency / database is
   locked", "sync drift / silently lost books", "AI series hallucination",
   "orphaned uvicorn workers", "OAuth re-consent". A subsystem with five
   individually-reasonable patches is usually telling you the design is wrong,
   not that it needs a sixth patch. Say so where you see it.

4. **Follow the money.** Enumerate every code path that can reach
   `AnthropicIdentificationClient` (identify, identify_series,
   resolve_book_request, describe, propose_series_merge, audit_book_identity).
   For each: is it bounded, cached (keyed correctly), and opt-in where it
   should be? Can a scan, a nightly run, or a `/loop` fan out unboundedly?
   Is the `ai_decisions` cache ever silently missed (evidence-hash instability)?

5. **Security & privacy.** The viewer now grants **full** `drive` scope to
   everyone who signs in (guest/read-only mode was removed) — is that still the
   right call, and is anything writing to Drive that a typical family member
   wouldn't expect? The Drive sidecar JSON files are plaintext and readable by
   anyone with folder access — is anything sensitive in them (the activity log
   names who searched for what)? Check token encryption (`app/core/crypto.py`,
   `token_encryption_key`), `.env` / `.env.example`, and whether anything
   secret lands in a build artifact or the committed history. Check the baked
   defaults in `library-viewer/src/lib/config.ts` are genuinely public-safe.

6. **Data integrity — hunt the next silent-loss bug.** The incremental Drive
   changes-feed sync + its 24h auto-rebuild safety net; same-book duplicate
   detection; sticky corrections keyed on `sha256` / `original_sha256`; the
   fuzzy find-or-create races in `book_repository` / `series_merge_service` and
   their module-level `asyncio.Lock`s (+ the `conftest.py` per-loop reset — is
   there a new singleton lock that forgot to add its reset?). Look for the next
   bug in the *same shape* as the ones already fixed.

7. **SPEC conformance.** Where has the implementation drifted from `SPEC.md`?
   For each drift: deliberate (and recorded in ROADMAP) or accidental? The
   README still says "EPUB Librarian" and omits `library-viewer` — note doc rot
   like that but don't drown the review in it.

8. **Dead weight & rot.** Unused exports, stale comments that describe removed
   behaviour (e.g. anything still mentioning `readOnly` / `SCOPE_READONLY` /
   guests), half-built or reverted features (`.cbz` convert-then-revert, the
   admin-app wishlist that exists twice), TODO rot.

9. **Frontend.** Both apps: error-handling gaps in the "lost the job id, button
   stuck on 'running…' forever" class; unhandled promise rejections; the
   `set-state-in-effect` pattern that's spread through the admin pages;
   accessibility basics (labels, focus, keyboard). The viewer's offline/cached
   behaviour and the "Failed to fetch" surfacing.

10. **Deferred / reverted calls.** Anything parked in ROADMAP "Later / maybe" —
    is it still the right call, or has the situation changed?

## Severity rubric

- **P0** — live data-loss, security, or credit-burn risk. Someone could lose
  books, leak data, or run up a bill *today*.
- **P1** — a correctness bug or fragile design that will bite with normal use.
- **P2** — worthwhile cleanup, a real coverage gap, or drift worth correcting.
- **P3** — nice-to-have; list briefly, don't write a prompt for it.

## Output

1. **The written review**, in the session and saved as `REVIEW-<YYYY-MM-DD>.md`
   at the repo root: (a) a short "what's healthy" section — this project has a
   lot of genuinely careful work in it, say so specifically; (b) the system
   map; (c) findings grouped P0→P3, each with `file:line`, what breaks and
   under what conditions, and a specific recommended fix.

2. **A work-prompt file per P0–P2 finding**, in `prompts/`. Match the house
   style of the existing ones (`## Why / ## Goal / ## Where it goes /
   ## Acceptance criteria / ## Gotchas`). Each must be runnable cold in a fresh
   session — its own context, its own acceptance criteria, a pointer to the
   shared conventions in `prompts/README.md`. Number them continuing from the
   existing set (next is `06-…`) or start a dated batch; update the table in
   `prompts/README.md`.

3. End with the list of prompt files you created and a recommended running
   order (dependencies, and which to do first for risk reduction).

## Definition of done for the review itself

- All three test suites actually ran; results reported honestly, skips named.
- Every finding has `file:line` evidence and a concrete fix — no vibes.
- No finding duplicates something already shipped (checked against ROADMAP
  "Done" + `git log` + existing `prompts/`).
- Nothing was edited, migrated, committed, or deployed.
- No Anthropic call and no real-Drive write happened at any point.
