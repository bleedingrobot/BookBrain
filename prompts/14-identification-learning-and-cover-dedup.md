# Task 14 — Learn from corrections; catch re-uploads by cover art

> **Status: shipped.** C in `c309245` (few-shot corrections, +~209 tok worst
> case). D in a follow-up commit (`files.cover_phash` + Library Audit
> "Near-identical cover art" panel, Hamming ≤ 6). See ROADMAP + the
> `project_bookbrain_ai_series_hallucination` memory.

Read `prompts/README.md` first for shared context. This is the **C + D**
split-off from `prompts/13-trustworthy-identification.md` — A and B shipped in
commit `351850e` (series_number clamp + `UNCORROBORATED_SERIES_PENALTY`). Read
prompt 13's "Why" section; the same wound is the reason for both pieces here.

C and D are **independent** — do them in either order, commit each on its own.
C is the higher-leverage one (it stops the model repeating known mistakes); do
it first unless D is quicker to land.

---

## C. Correction memory as few-shot examples

### Why

`/correct` today produces two things: a sticky `Review(status=corrected)` row
and — only when `apply_to_similar` is set — an exact-match `library_rules`
alias. The alias only fires on a byte-for-byte pattern match in
`find_rule_match`. Nothing teaches the *model* anything, so the next rescan of
a *similar* book (same author, same invented-series trap) makes the same
mistake, and James corrects it again. The review DB is full of clean
`(what the AI said → what a human fixed it to)` pairs that never get used.

### What exists

- `Review` rows with `status = corrected` hold `proposed_json` and
  `correction_json`. **Two writers**, slightly different `proposed_json`:
  - `review_service.correct` — `proposed_json` is the AI's original proposal
    (has `reasoning_summary`, `computed_confidence`).
  - `file_service.correct_file` — `proposed_json` is the *previous book state*
    (title/author/series/series_number only). Still a valid wrong→right pair.
  Both `correction_json` shapes are `{title, author, series, series_number}`.
- `identification_service._build_prompt(filename, evidence, candidates)` — a
  module-level function, builds the `identify_book` prompt. The AI path only.
- `IdentificationService.identify` is **deliberately session-free** (SPEC
  design — it takes evidence + candidates, nothing else). Don't add a session.
- `scan_service._process_file` already holds an `async_session_factory()`
  session right before it calls `identify` (the `find_rule_match` block,
  `scan_service.py` ~544). That's where the corrections get fetched.

### Goal

1. **`review_service.recent_corrections(session, *, limit=5, author=None,
   series=None)`** → `list[CorrectionExample]` (a small dataclass or a plain
   dict is fine): recent `Review` rows where `status == corrected` and
   `correction_json` is non-null. Rank:
   - first, rows whose `proposed_json` **or** `correction_json` author matches
     `author` (normalized), or series matches `series`;
   - then most-recent by `resolved_at`.
   Return at most `limit`. Skip rows where proposed == corrected on every
   field (a no-op correction teaches nothing).

2. **`_build_prompt` gains `corrections: list[...] | None = None`** (keyword,
   defaulted — so the no-corrections prompt and its `prompt_hash` are
   unchanged). When non-empty, append one short section:

   ```
   Corrections a human has previously made to your identifications. Learn from
   these — especially: do not invent a series for a standalone book.
   - You said: "Scion" by James Islington, series "The Hierarchy" #2.
     Corrected to: standalone, no series.
   - You said: "<title>" by <author>, series "<x>" #<n>.
     Corrected to: <...>.
   ```

   Only render fields that actually changed. Standalone corrections (series
   nulled) are the point — phrase those explicitly as "standalone, no series".

3. **`IdentificationService.identify` gains `corrections=` passthrough** to
   `_build_prompt`. Fast path and the `identify_series` lookup ignore it —
   they don't call the model. `find_rule_match` never reaches `identify`.

4. **`scan_service._process_file`**: in the existing session block, call
   `review_service.recent_corrections(session, author=<first evidence
   author>, series=<evidence.series>)` and pass the result into
   `identify(..., corrections=...)`. Only when the AI path will run (i.e.
   `identification is None` after `find_rule_match`).

5. **Measure the prompt-size delta.** AGENTS.md /
   `feedback_maths_alignment_tool_claude_prompt_size` applies — this is the
   same "don't bloat the prompt blind" rule. Print/log `len(prompt)` with and
   without the section on a realistic 5-example set; put the number in the
   commit message and the ROADMAP note. Hard cap: **5 examples**, and truncate
   any single title/series to keep the section under ~400 tokens (~1600 chars).

### Gotchas

- **Don't feed the fast path or `identify_series`.** Only `_build_prompt`.
- **`prompt_hash` will now vary with correction history.** That's fine —
  `prompt_hash` is stored, never used as a cache key (`evidence_hash` +
  `file_id` is the dedup index, and `evidence_hash` is unaffected). Note it in
  the commit message so it isn't a surprise later.
- **No real Anthropic calls in tests.** `test_identification_service.py` uses
  `_FakeAIClient` — extend that. `test_scan_service.py` mocks the identify
  service; keep it mocked.
- **Empty / missing corrections must produce the byte-identical old prompt.**
  Add a test asserting `_build_prompt(...)` with `corrections=None` and with
  `corrections=[]` equals the current output.
- **Privacy / size:** titles and author names only — never dump
  `reasoning_summary` or confidence numbers into the prompt.

### Acceptance criteria

- `recent_corrections` unit test: author-match rows rank ahead of newer
  non-matching rows; no-op corrections excluded; `limit` respected.
- `_build_prompt` includes the block when examples are passed, omits it
  cleanly (identical to today) when not; a "standalone, no series" correction
  renders as such.
- `test_scan_service`: a scan with a corrected review present still identifies
  correctly (AI mocked) and the fetched corrections reach the identify call.
- Prompt-size delta measured and recorded in the commit message + ROADMAP.
- `cd backend && pytest` green; `alembic check` clean (no schema change).
- Committed + pushed. Update `project_bookbrain_ai_series_hallucination` and
  `project_bookbrain_review_followups` memories.

---

## D. Perceptual-hash cover dedup

### Why

`detect_same_book_duplicates` matches on `sha256` only — a re-upload with
mangled/rewritten metadata (different bytes) sails right past it and lands as a
second, separately-identified book. But the **cover art is usually
byte-for-byte the same image**, or close. `cover_service` already renders a
JPEG thumbnail per organised file; a perceptual hash of that thumbnail is a
cheap second signal for "these two different-identified files are probably the
same book".

### What exists

- `cover_service._make_one` (sync, runs in a thread via `asyncio.to_thread`,
  **no DB session**) downloads the book, extracts a cover, builds `thumb`
  (JPEG bytes), uploads it to Drive as `<driveFileId>.jpg`, or uploads a
  0-byte `<driveFileId>.nocover` marker.
- `regenerate_covers` *does* have a session (for the "which files still need a
  cover" query) but it closes before the `asyncio.gather` of `_make_one` runs.
- `File` has no cover column. Indexes on `files`: `ix_files_sha256` (implicit,
  `sha256` `index=True`), `ix_files_original_sha256`, `ix_files_sha256_status`
  (explicit `Index`), plus `unique` on `drive_file_id`.
- `library_audit_service.audit_library` — read-only, returns
  `LibraryAuditResult` with `similar_series` / `similar_authors` cluster
  lists. Frontend: `frontend/src/pages/LibraryAudit.tsx` (admin app, **not**
  library-viewer). This is where D surfaces.
- Pillow is already a dependency (`from PIL import Image` in `cover_service`).

### Goal

1. **`pip`-add `imagehash`** (`backend/pyproject.toml`). It depends on Pillow
   (already present) + numpy (check whether numpy is already transitive; if
   not, it comes with imagehash).

2. **`files.cover_phash: Mapped[str | None]`** — hex string of a 64-bit
   pHash (`str(imagehash.phash(img))`, 16 hex chars). Add to `models.File`.
   **Migration:** a plain `op.add_column('files', sa.Column('cover_phash',
   sa.String(), nullable=True))` — SQLite supports `ALTER TABLE ADD COLUMN`,
   so **no `batch_alter_table` recreate is needed** (prompt 13's "third
   `batch_alter_table` recreate" note was wrong for a plain nullable add —
   only altering existing columns/constraints needs the rebuild). Follow the
   `d1e2f3a4b5c6` recipe (which adds `original_sha256` the same way). Run
   `alembic history` first to get the real current head to revise from
   (`f0e1d2c3b4a5` at time of writing). No index needed — the dedup pass is a
   full scan of a few thousand short strings; add one only if it proves slow.

3. **Compute it during cover generation.** `_make_one` returns a status
   string today (`"done"` / `"nocover"`). Change it to return
   `(status, phash | None)`; compute `imagehash.phash` from the same `Image`
   you already open in `_thumbnail` (refactor `_thumbnail` to also hand back
   the `Image`, or compute the hash inside it). Collect
   `{drive_file_id: phash}` from the `run()` tasks and, **after**
   `asyncio.gather`, do one bulk `UPDATE files SET cover_phash = ...` in a
   fresh session. `.nocover` files get `cover_phash = NULL`.

4. **Backfill:** `regenerate_covers` already re-renders any file missing a
   cover. Add: also re-render (or at least re-hash from the existing Drive
   `.jpg`) files where `cover_phash IS NULL` but a `.jpg` exists. Simplest:
   in the "missing" query, treat "has a `.jpg` in Drive but `cover_phash IS
   NULL` in the DB" as also needing a pass. Keep it bounded by the existing
   `limit`.

5. **Surface it — Library Audit only, never an auto-action.** New
   `library_audit_service` section (extend `LibraryAuditResult` with
   `similar_covers: list[...]`): pairs of files that
   - resolve to **different** books (`file.book_id` differs, and the books
     aren't already a known duplicate/same_book pair), and
   - have `cover_phash` within **Hamming distance ≤ 6** of each other.
   Return the two filenames, the two book titles, and the distance. Add a
   panel to `LibraryAudit.tsx` listing them ("Possible same book — near-
   identical cover art"). No apply button in this task — the human uses the
   existing per-file `/correct` to merge them. (A future prompt can add a
   one-click merge.)

### Gotchas

- **Publisher-template / plain-text covers pHash-collide** — Tor, Baen, many
  self-pub. Keep the threshold tight (≤ 6, tune down if the real library is
  noisy) and **exclude any file whose cover is the `.nocover` placeholder**
  (its `cover_phash` is NULL — just skip NULLs, which falls out naturally).
- **O(n²) pair scan.** A few thousand files is fine as a nested loop in
  Python (a few million cheap int-xor+popcount ops). If it's slow, bucket by
  the top 16 bits of the hash first. Don't reach for a BK-tree yet.
- **`_make_one` runs in a thread with no event loop / session** — it must
  stay pure-CPU + Drive I/O and *return* the hash, not write it. All DB
  writes happen back on the asyncio side after `gather`.
- **Migration is a plain add_column** — but still run the full
  `test_scan_service` / `test_duplicate_service` / `test_migrations` suites
  after (they assert the `ix_files_*` indexes survive any `files` change).
- **Don't add `create_constraint=True`** to anything (standing rule from
  `b2c3d4e5f6a7`).
- **numpy import cost** — `imagehash` pulls numpy. Import it inside
  `cover_service` (module top is fine, it's not a hot path) and confirm the
  test-suite startup time doesn't jump.

### Acceptance criteria

- `alembic upgrade head` + `alembic check` clean; `downgrade` round-trips;
  `ix_files_sha256`, `ix_files_sha256_status`, `ix_files_original_sha256`
  intact (`test_migrations` covers this).
- `cover_phash` populated on cover generation (`test_cover_service` — mock the
  Drive provider, feed a real small JPEG, assert a 16-hex-char hash lands on
  the `File` row). `.nocover` files stay NULL.
- Backfill fills `cover_phash` for a file that has a `.jpg` but no hash.
- `audit_library` returns a `similar_covers` pair for two seeded
  different-book files with near-identical cover hashes; does **not** return a
  pair when the distance is > 6; never includes a NULL-hash file.
- `LibraryAudit.tsx` renders the new panel; `cd frontend && npm run build`
  passes.
- `cd backend && pytest` green. Committed + pushed. ROADMAP + memory updated.

---

## When both are done

- Update `prompts/README.md`: move 13 out of "Open", add 14, mark both done
  with SHAs (mirror the batch tables' format).
- `project_bookbrain_review_followups` memory: note batch-13/14 complete.
- `project_bookbrain_ai_series_hallucination` memory: the reactive→proactive
  arc is now A (clamp) + B (uncorroborated penalty) + C (few-shot) + D (cover
  dedup). The one remaining open item is whether
  `reident_audit_service._recompute_confidence` should pass `resolved_series`
  (retroactively penalise historical books) — still deliberately off.
