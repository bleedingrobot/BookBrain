# Task 13 — Proactive guards against AI mis-identification (hardening)

Read `prompts/README.md` first for shared context.

## Why

Every review and half the memory notes on this project circle back to the same
wound: the AI **invents** identification data — a series for a standalone book, a
junk `series_number` from a Calibre placeholder ("Alexis Carew #301"), a
plausible-but-wrong volume number. `claude-opus-5` frequently *reasons correctly*
("#301 is almost certainly a placeholder") and then **emits the bad value
anyway**.

Everything built so far is **reactive**: per-book `/correct`, series merge, the
Bulk Re-identify Audit, the batch-06 strict title matcher. Nothing stops a bad
identification from **auto-organising silently** in the first place.

`confidence_service.score()` only penalises source *disagreement*
(`PROVIDER_DISAGREEMENT_PENALTY`, `SERIES_DISAGREEMENT_PENALTY` — both require two
sources that *conflict*). It has no penalty for a field the AI asserted into a
**gap** where no source said anything at all. So "Scion" got series
`"The Hierarchy" #2` with a computed confidence of 85 = `confidence_auto_flagged`
→ auto-organised, `needs_human_review=0`, even though the raw AI response itself
set `needs_human_review: true` (ignored by design — AI self-assessment is
advisory only).

`reident_audit_service._series_corroborated()` /
`_divergence_for()` already implement exactly the "is this series backed by the
EPUB, a stored candidate, or a provider?" check — but only *after the fact*, as a
report. This task moves that logic **forward** into the pipeline.

## Goal

Four independent pieces, roughly in leverage order. A and B are the point of the
task — ship those even if C/D slip to a follow-up prompt.

### A. `series_number` sanity clamp at identification time

The review's own recommended "third defensive layer", never built.

- A small pure helper (e.g. `identification_service._sane_series_number(series,
  n)` or a new `app/services/metadata_sanity.py`): given the resolved `series`
  and `series_number`, return a cleaned number.
  - `series is None` → `series_number` must be `None`.
  - `n < 0` or `n > 50` → `None` (keep the series name; it's the *number* that's
    junk). 50 is deliberately generous — real >50-volume series are rare and a
    human can `/correct` the edge case.
  - A fractional number (`3.5`, novella-style) is **legitimate** — don't reject
    those.
- Apply it in **`identification_service.identify`** to the final
  `series_number` on **every** return path: the fast path (from a candidate *or*
  from `identify_series()`), the AI path, and the `find_rule_match` /
  fallback paths in `scan_service` if they set one.
- When a value is clamped, record it: add `"series_number_clamped": <original>`
  to `raw_response` so the Re-identify Audit and Activity trail can show it, and
  log it at INFO.

### B. Confidence penalty for an uncorroborated series (structural gap #1)

The single highest-leverage change — it turns "silent misfile" into "James
glances at it".

- In `confidence_service.score()`, add a penalty
  (`UNCORROBORATED_SERIES_PENALTY`, start at `-15`) that fires when **the
  resolved book has a series that appears in neither the EPUB metadata nor any
  provider candidate**. This is distinct from the existing
  `SERIES_DISAGREEMENT_PENALTY` (which needs a *conflicting* candidate series) —
  the new one fires on *silence*.
  - `score()` currently only sees `evidence` + `candidates`, not the resolved
    result. Add a `resolved_series: str | None` (or `ai_supplied_series: bool`)
    parameter so it can compare. Default it so existing callers
    (`reident_audit_service._recompute_confidence`, tests) don't change
    behaviour unless they opt in.
  - Match "appears in a source" with `normalize_words` (same as
    `_series_corroborated`), not exact string equality.
- **`identification_service.identify` fast path**: `score()` is currently called
  *before* the `identify_series()` lookup (line ~51), so its result predates the
  AI-supplied series. Re-score (or apply the penalty delta) **after**
  `identify_series()` returns, passing the resolved series, so a fast-path book
  that only has a series because the model guessed one drops toward / under the
  `confidence_auto_flagged` (85) bar → lands in review.
- Consider the same treatment for a **subtitle the AI added that no source has**
  (`"<Title>: <thing>"` where no candidate title carries the `:` part) and for
  an **AI-supplied ISBN with no corroboration** — but series is the proven
  offender; do those only if they fall out cheaply.
- Expected effect: a clean ISBN+provider+EPUB match (≈90) minus an
  uncorroborated-series `-15` = 75 → review queue. A genuinely well-sourced book
  is unaffected. Verify against the existing `test_confidence_service` /
  `test_identification_service` fixtures — some may legitimately need their
  expected totals updated; a few *should* now flip to `needs_human_review`.

### C. Correction memory as few-shot examples

`/correct` today only creates exact-match `library_rules` (author/series alias).
The model keeps re-making the same mistake on the next rescan of a similar book.

- A `Review` row with `status = corrected` already holds `proposed_json` (what
  the AI said) and `correction_json` (what the human said) — a perfect
  `(wrong → right)` pair. `file_service.correct_file` writes these too.
- New helper (`review_service.recent_corrections(session, *, limit=8,
  author=None)` or similar): return recent corrected pairs, preferring ones
  whose `proposed`/`corrected` author or series matches the book currently being
  identified, then most-recent.
- Inject into `identification_service._build_prompt` as a short section:

  ```
  Corrections a human has previously made to your identifications — learn from
  these, especially about inventing a series for a standalone book:
  - You said: series "The Hierarchy" #2 for "Scion" by James Islington.
    Correct:  standalone, no series.
  - ...
  ```

- `IdentificationService.identify` has no DB session (kept deliberately
  session-free). Don't add one — have **`scan_service._process_file`** fetch the
  corrections under its existing session and pass them into `identify(...,
  corrections=...)`.
- **Measure the added prompt size** before and after (AGENTS.md / the
  `feedback_maths_alignment_tool_claude_prompt_size` rule applies here too — this
  is the same "don't bloat the prompt blind" discipline). Cap at ~5 examples /
  ~400 tokens. Only inject when there are corrections to show.
- Only feed the **AI path** (`_build_prompt`). The fast path and `find_rule_match`
  don't call the model.

### D. Perceptual-hash cover dedup (can be its own follow-up)

Title/author matching misses a re-upload with mangled metadata — but the cover
art is usually identical. `cover_service` already renders a JPEG thumbnail per
file.

- Add `imagehash` (Pillow is already a dep). Compute a pHash when a thumbnail is
  generated in `cover_service._make_one`.
- Store it: new `files.cover_phash: str | None` column. **Migration** =
  `batch_alter_table("files")` on SQLite, **same recipe as
  `alembic/versions/b2c3d4e5f6a7`** (batch-08); re-verify the three
  `ix_files_*` indexes survive.
- Backfill: `regenerate_covers` fills `cover_phash` for files missing it (it
  already re-downloads / re-renders).
- Use it: a new signal — either a `duplicate_service` check or a Library Audit
  section — flagging **two files that resolve to different books but whose cover
  pHash is within Hamming distance ≤ 6**. Surface as a review item, never an
  auto-action.
- **Gotchas:** plain-text / publisher-template covers pHash-collide (Tor, Baen,
  self-pub) — keep the distance threshold tight and exclude the
  `<id>.nocover` placeholder entirely. Expect to tune the threshold against the
  real library; ship it behind the Library Audit page (a suggestion list), not
  anywhere that deletes.

If D balloons, ship A+B+C and file D as `prompts/14`.

## Where it goes

- `backend/app/services/identification_service.py` — A (clamp), B (re-score),
  C (`corrections=` param + prompt section).
- `backend/app/services/confidence_service.py` — B (new penalty + param).
- `backend/app/services/metadata_sanity.py` *(new, optional)* — A helper.
- `backend/app/services/scan_service.py` — pass corrections into `identify()`;
  apply the clamp on any fallback path that sets `series_number`.
- `backend/app/services/review_service.py` — C (`recent_corrections`).
- `backend/app/services/cover_service.py`, a new migration,
  `backend/app/data/models.py`, `duplicate_service.py` /
  `library_audit_service.py` + `frontend/src/pages/LibraryAudit.tsx` — D.
- `backend/app/providers/ai/schema.py` — optionally tighten the `series_number`
  description in `IDENTIFY_BOOK_TOOL` ("only if this is a genuine numbered
  volume; leave null for a standalone or a placeholder number") while you're
  here.
- Tests: `test_confidence_service.py`, `test_identification_service.py`,
  `test_scan_service.py`, `test_review_service.py`, `test_cover_service.py`,
  `test_migrations.py` (D).

## Acceptance criteria

- **A:** `series_number` of 301 (series present) → stored as `null`, series name
  kept, `raw_response` records the original; `3.5` is untouched; `series=None`
  forces `series_number=None`. Covered by a unit test.
- **B:** new test — an ISBN+provider+EPUB match where the AI (or `identify_series`)
  adds a series that no candidate and the EPUB don't mention → computed
  confidence drops below `confidence_auto_flagged` and `needs_human_review` is
  true. A book whose series *is* in a candidate is unaffected. Existing
  confidence tests updated where the new penalty legitimately changes a total.
- **C:** `_build_prompt` includes a corrections block when corrected reviews
  exist and omits it cleanly when none do; the added size is measured and noted
  in the commit message / ROADMAP; a scan with corrections present still
  identifies correctly (mock the AI client — **no real Anthropic calls in
  tests**).
- **D:** `cover_phash` populated on cover generation; `alembic upgrade head` +
  `alembic check` clean, downgrade round-trips, `ix_files_*` intact; a seeded
  pair of different-book files with near-identical cover hashes shows up in the
  audit; the `.nocover` placeholder never does.
- All three suites green; `cd backend && alembic upgrade head && alembic check`
  clean. ROADMAP updated; update
  `project_bookbrain_ai_series_hallucination` memory (this closes structural
  gap #1 and adds the clamp + few-shot).
- Committed and pushed (per-piece commits preferred: `A+B` together, then `C`,
  then `D`).

## Gotchas

- **Don't route on the AI's self-reported confidence.** `computed_confidence`
  stays authoritative (SPEC §1). B lowers the *computed* score; it must not read
  `ai_reported_confidence`.
- **`reident_audit_service` already recomputes `score()`** for its display
  cross-check (`_recompute_confidence`). If B adds a required parameter, give it
  a safe default so that call site (and the deterministic recompute path) don't
  silently start penalising every historical book — decide deliberately whether
  the reident recompute *should* apply the new penalty (probably yes, but as a
  separate, explicit change with its own test).
- **The fast-path `identify_series` call is by design uncorroborated** — that's
  the whole point of B. Don't special-case it out; a model-guessed series with
  no backing *should* pull the score down.
- **C must not turn into prompt bloat.** Measure. The
  `feedback_maths_alignment_tool_claude_prompt_size` discipline is in AGENTS.md
  for a reason — a few well-chosen examples, not the whole correction log.
- **D's migration is the third `batch_alter_table("files")` recreate** (after
  batch-08 and any since). Run the full `test_scan_service` / `test_duplicate_service`
  suites after, not just `alembic check`. Don't add `create_constraint=True` to
  any Enum.
- Nightly auto-organises torrented books unattended — B's whole value is that a
  hallucinated series now parks those in review instead of the library. Confirm
  the nightly path (`jobs/nightly` → `run_scan` → `_auto_organize`) respects the
  lowered confidence (it routes on `needs_human_review` / status, so it should).
