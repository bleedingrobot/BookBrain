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

## Shared context (every session should know this)

- **Repo:** `C:\Users\Giant\Documents\epub-librarian` — the directory keeps the
  old name; the project is **BookBrain**. Read `README.md`, `SPEC.md`,
  `ROADMAP.md`, and `AGENTS.md`/`CLAUDE.md` if present before starting.
- **Three apps:**
  - `backend/` — FastAPI + SQLAlchemy 2.0 async + Alembic, SQLite. Strict layering
    `api/ → services/ → providers/ → data/`. `cd backend && pytest`. Dev server
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
