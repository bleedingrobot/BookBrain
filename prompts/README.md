# Work prompts

Each file is meant to be run as its **own fresh Claude Code session** (start a
new chat, paste the file's contents or say "follow `prompts/NN-*.md`").

## Recurring

- [`review.md`](review.md) — a full read-only project review. Produces a written
  assessment and a fresh batch of numbered work-prompts. Run it every few weeks
  or after a burst of feature work.

## 2026-09-06 review batch (all shipped)

Ran in this order — 3 and 5 leaned on 1 and 2, but none was a hard dependency:

| # | File | One line |
|---|------|----------|
| 1 | [`01-ship-series-merge.md`](01-ship-series-merge.md) | Review, test and commit the uncommitted `series-merge` / library-audit work |
| 2 | [`02-scheduled-runs.md`](02-scheduled-runs.md) | Nightly unattended scan → organize → covers → index |
| 3 | [`03-epub-metadata-writeback.md`](03-epub-metadata-writeback.md) | Write the resolved title/author/series + cover into the EPUB itself |
| 5 | [`05-bulk-reidentify-audit.md`](05-bulk-reidentify-audit.md) | Re-check every organised book's identification, report what changed |

## 2026-09-06 review batch #2 (`REVIEW-2026-09-06.md`) — all shipped

Worked in one session via `prompts/12` (order 06 → 08 → 07 → 10 → 09 → 11),
commit per stage. SHAs + details in `REVIEW-2026-09-06.md` §"Shipped 2026-09-06".

| # | File | Sev | One line |
|---|------|-----|----------|
| 06 | [`06-title-collision-false-duplicates.md`](06-title-collision-false-duplicates.md) | P0 | `normalize_title` merges distinct books → `same_book` false positives → bulk trash |
| 07 | [`07-librarysync-missing-parents.md`](07-librarysync-missing-parents.md) | P1 | Viewer sync drops a cached file when a Drive change record omits `parents` |
| 08 | [`08-alembic-enum-drift.md`](08-alembic-enum-drift.md) | P2 | Migration for the `status`/`status_reason` enum additions; wire `alembic check` in |
| 09 | [`09-organize-write-lock.md`](09-organize-write-lock.md) | P2 | `OrganizeService._write_lock` not reset per test; unify the write locks |
| 10 | [`10-series-merge-undo.md`](10-series-merge-undo.md) | P2 | Series-merge Operations logged as undoable but Undo leaves a broken state |
| 11 | [`11-ai-spend-guardrails.md`](11-ai-spend-guardrails.md) | P2 | Cap + cost estimate for `descriptions?ai=true` and rebuild |

`REVIEW-2026-09-06-FIXPLAN.md` (repo root) resolves the open design choices in
06–11. [`12-work-the-review-fixes.md`](12-work-the-review-fixes.md) is the
staged, commit-per-stage prompt that works through all six in one session
(order: 06 → 08 → 07 → 10 → 09 → 11).

## Trustworthy identification (`prompts/13` + `14`) — all shipped

| # | File | Kind | One line |
|---|------|------|----------|
| 13 | [`13-trustworthy-identification.md`](13-trustworthy-identification.md) | hardening | **A + B** (`351850e`): `series_number` clamp + `UNCORROBORATED_SERIES_PENALTY` (structural gap #1). C + D split to prompt 14 |
| 14 | [`14-identification-learning-and-cover-dedup.md`](14-identification-learning-and-cover-dedup.md) | hardening | **C** (`c309245`): recent `/correct` pairs fed into the identify prompt as few-shot (+~209 tok worst case). **D** (`edff935`): `files.cover_phash` + "Near-identical cover art" panel in Library Audit |

## First-pass identification accuracy push (`prompts/15`) — COMPLETE 2026-09-07

All stages shipped: 0 (harness) + A–D (Tier 1) + E–H (Tier 2) + I/J/K (Tier 3).
See `15-identification-accuracy-push.md` Sequencing block, `IDENTIFICATION-EVAL.md`,
and `SPEC.md` § "Identification pipeline (2026)".

| # | File | Kind | One line |
|---|------|------|----------|
| 15 | [`15-identification-accuracy-push.md`](15-identification-accuracy-push.md) | umbrella / multi-session | Get first-scan identify+name+file accuracy toward ~100%. **Stage 0 landed & redesigned autonomous** (James wanted zero manual verification): 74-book corpus with **triangulated** answer keys (`scripts/build_truth.py` — Wikidata + 2 web-grounded Claude calls; a field counts only when ≥2 independent sources agree), `pytest -m corpus` gate, plus `test_identification_invariants.py` + `test_identification_mutation.py` (need no ground truth). Baseline is **partial** — API credit ran out mid-`build_truth`; re-run to complete. **Stage A shipped (2026-09-06)**: web-search grounding on the AI identify turn (`identify(prompt, ground=)` + `web_search_20260209` + `should_ground()` gate + `settings.ai_web_search_enabled`); offline corpus unchanged by construction, live measurement pending credit. **Stage B shipped (2026-09-06)**: Google Books + Open Library now populate `MetadataCandidate.series` / `series_number` / `genre` (F1); `SERIES_DISAGREEMENT_PENALTY` needs a provider consensus now. **Stage C shipped (2026-09-06)**: `providers/filename/parser.py` structured inbound-filename parse → labelled prompt block + `filename_corroborates` verdict replacing the weak substring test (F2); corpus `wrong_auto_organized` 2→1. **Stage D shipped (2026-09-06)**: spine-walking text snippet (`[front matter]` + `[body sample]`, skips cover/nav) + `EpubEvidence.publisher`/`pub_date`/`subjects`/`all_isbns` + `description` and all four into `_build_prompt`; `hash_evidence` untouched so the cached AI decisions stay valid. **Tier 1 complete.** **Tier 2 complete (2026-09-06)**: E placeholder/junk-metadata detector (fast-path skip + `PLACEHOLDER_METADATA_PENALTY`), F ISBN-trust check (`title_similarity ≥ 0.80` on the fast path), G positive confidence components (`DESCRIPTION_CORROBORATES` / `PUBYEAR_PLAUSIBLE`, additive) + `resolved_series` threaded through reident recompute, H verification pass (one adversarial `audit_book_identity` call for the 70–95 band, `settings.ai_verify_enabled` **off by default**). All offline-flat (frozen AI); E/F/G no AI cost, H opt-in. **Tier 3**: **J + K shipped (2026-09-07)** — J: `normalize_person_name` author match key + `Author.sort_name` + article-insensitive series match + `SeriesAlias` consulted/written on merge + dry-run repair/backfill scripts. K: `batch_prior_service` — a ≥3-file author/series consensus in a scan lifts a `review` file whose filename names it (+12, cap 92), before the auto-organize pass. Both corpus-flat (harness starts empty / scores one file at a time). Still to do: **I** (recently-auto-organized Dashboard tray + `settings.organize_hold_hours` soft-hold) — split into its own prompt below. One commit/stage. |
| 16 | [`16-stage-i-recently-organized-tray.md`](16-stage-i-recently-organized-tray.md) | prompts/15 Stage I | **DONE 2026-09-07.** `GET /api/library/recently-organized` + `recently_organized_service` + `RecentlyOrganized.tsx` Dashboard tray (Confirm/Correct/Confirm-all, 24h/48h/7d toggle); `POST /api/files/{id}/confirm` + `/files/confirm-batch` = idempotent `Review(approved)` signal; `settings.organize_hold_hours` soft-hold (default 0 = byte-identical no-op, one `discovered_at` WHERE clause) folded into `/settings/organize`. `Operation.confidence`/`model` now populated by organize. No AI cost. |

## Recent

| # | File | Kind | One line |
|---|------|------|----------|
| 17 | [`17-library-viewer-epub-reader.md`](17-library-viewer-epub-reader.md) | feature — `library-viewer` only | **§A–C DONE 2026-09-07** (`e08b700` + `511d2cf`). Vendored `foliate-js` (EPUB path, no npm dep) → `components/Reader.tsx`: full-screen paginated reader, tap/key/swipe, Contents drawer, Display panel (`readerPrefs.ts`), position in `localStorage` (`readingProgress.ts`), IndexedDB offline byte-cache (`bookCache.ts`, LRU 300 MB/20). "Read" on `.epub` rows + "Continue reading" strip + "Clear downloaded books". Build/lint/90 tests green; James-verified on real devices (`2ad7e40` + `c9c4c6b` follow-up fixes). **Considered complete.** §D (word-count→time-left), §E (cross-device position sync), Kobo sync — all dropped (James); position stays per-device, progress shown as `%`. |

## Shared context (every session should know this)

- **Repo:** `C:\Users\Giant\Documents\epub-librarian` — the directory keeps the
  old name; the project is **BookBrain**. Read `README.md`, `SPEC.md`,
  `ROADMAP.md`, and `AGENTS.md`/`CLAUDE.md` if present before starting.
- **Three apps:**
  - `backend/` — FastAPI + SQLAlchemy 2.0 async + Alembic, SQLite. Strict layering
    `api/ → services/ → providers/ → data/`. `cd backend && pytest` (includes
    `tests/test_migrations.py`, which runs `alembic upgrade head` + `alembic check`
    in a subprocess — schema drift fails the suite). Dev server
    `uvicorn app.main:app --reload` on `:8000`.
  - `frontend/` — React + TS + Vite + TanStack Query + Tailwind v4, the local
    admin UI. `cd frontend && npm run dev` on `:5173`, proxies `/api` to `:8000`.
    `npm run build` to typecheck.
  - `library-viewer/` — separate static React app, the family-facing browser.
    Deployed to GitHub Pages by a **GitHub Actions workflow on push to `main`**.
    `cd library-viewer && npm run build && npx vitest run && npm run lint`.
- **Deploy:** only `library-viewer` deploys (on push to main). `backend`/`frontend`
  run locally — nothing to deploy, but still build + test before committing.
- **Git:** work happens directly on `main`. Commit + push proactively when the
  task is green (build + tests pass). End commit messages with:
  `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>`
- **The user (James) is not terminal-savvy** — run commands yourself, don't hand
  him a list to type. He tests against a running app, not by reading diffs.
- **Windows gotchas that have bitten before:**
  - Orphaned `uvicorn --reload` workers hold the SQLite file → "database is
    locked". Check for and kill stragglers before blaming code.
  - A long `BackgroundTask` under `uvicorn --reload` can wedge the dev server.
  - Services serialize their SQLite writes with a **module-level `asyncio.Lock`**;
    `conftest.py` resets those per-test because `pytest-asyncio` gives each test
    its own event loop and a lock binds to the loop of its first acquire. If you
    add a new singleton lock, add a reset for it in `conftest.py`.
- **AI:** `anthropic_model = claude-opus-5`. Structured output via forced tool
  schema. Known recurring failure: the model reasons correctly that a `series`
  value is bogus, then emits it anyway — see the memory note
  "BookBrain AI series hallucination".
- **App-computed confidence is authoritative** (SPEC §1) — never route on the
  AI's self-reported confidence.
