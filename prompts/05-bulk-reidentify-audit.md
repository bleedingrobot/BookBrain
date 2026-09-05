# Task 5 — Bulk re-identify audit

Read `prompts/README.md` first for shared context.

## Why

Library Audit (task 1) catches identity problems by comparing **DB row names**
(two `Series` rows that look alike). This task catches them by re-running
**identification** against current data and diffing the result against what's
stored. Different failure mode, same goal: surface old mistakes systematically
instead of one accidental discovery at a time.

Especially targets the recurring "AI series hallucination" (memory note): a book
confidently filed into a series that doesn't exist / isn't that book's series.

## Goal

A **read-only report**: for every organised book (has `book_id`, status
`organised`), re-derive what identification *would* say now and list the ones
where it diverges from the stored answer. Then James acts on each via the
existing `/correct` flow or a series merge.

Divergence signals to compute per book:
- Provider consensus (Google Books + Open Library, ISBN lookup first) now
  disagrees with the stored `title` / `author`.
- Stored `series` / `series_number` not corroborated by **any** provider and not
  from a `user_correction` / `library_rule` — i.e. likely an AI invention.
- The book's ISBN now resolves to a different work.
- Recomputed confidence (`confidence_service`) would fall below
  `confidence_auto_organize` (95) — it shouldn't be sitting in the library.
- Two organised books resolve to the same canonical identity (missed duplicate).

Each report row: book, stored answer, what re-identification says, which signal
fired, and the evidence, so the reason is always visible (SPEC §1 principle).

## Cost control — important

There are ~2200 organised books. Do **not** fan out 2200 Claude calls.

- Default pass = **free**: deterministic evidence + provider lookups +
  `confidence_service` + the **cached** `ai_decisions` (keyed by
  `sha256 + evidence_hash` — reuse, never regenerate). Zero API credits.
- The AI is only consulted for a bounded, opt-in "deep re-check" of rows the free
  pass already flagged — with a hard cap (e.g. 50/run) and a running credit
  estimate shown before it starts.
- Cache the report itself (it's expensive to build) — a `library_reident_report`
  table or a JSON blob in settings, regenerated on demand, with a "last run"
  timestamp. Mirror how Library Audit caches.

## Where it goes

- `app/services/reident_audit_service.py` — the report builder. Reuses
  `candidate_service`, `confidence_service`, `identification_service`,
  `sticky_resolution`. Batched, respects the services' write locks, runs as a
  tracked job (same job pattern as scan/covers).
- `GET /api/library-audit/reident` (or its own route) → cached report;
  `POST .../reident/rebuild` → kick off a rebuild job;
  `POST .../reident/deep-check` → bounded AI pass over flagged rows.
- Frontend: a section/tab on the **Library Audit** page — table of divergences,
  each row with "Correct…" (opens the existing `CorrectFileForm`) and, where
  relevant, "Investigate series merge" (task 1's flow). Dismiss/ignore per row,
  persisted like the audit cluster dismissals.

## Acceptance criteria

- Free pass over the whole library makes **zero** Anthropic calls (assert in a
  test with a mock that fails on call).
- Report correctly flags a seeded bogus-series book and a seeded wrong-title
  book; does not flag a correctly-identified one.
- Deep-check is capped, opt-in, shows a cost estimate, and only touches
  pre-flagged rows.
- Report is cached with a visible last-run time; rebuild is a tracked job.
- Divergence rows link into `/correct` and (for series) task 1's merge flow.
- Per-row dismiss persists.
- `cd backend && pytest` green; `cd frontend && npm run build` green.
- ROADMAP + memory note updated. Committed and pushed.

## Gotchas

- `evidence_hash` stability: if you recompute evidence slightly differently than
  the original pipeline, every `ai_decisions` cache row misses and you think
  everything diverged. Reuse the *exact* evidence-assembly function; add a test
  that a freshly-scanned book's evidence_hash matches what identification stored.
- Don't flag books whose stored answer came from `user_correction` or
  `library_rule` — the human already ruled, provider disagreement is expected.
- A provider being down (Google Books 429 is common — see README history) must not
  register as "divergence for every book" — treat "no provider data" as
  inconclusive, not as disagreement.
- Read-only. This task never moves a file or writes a book row — it only reports.
