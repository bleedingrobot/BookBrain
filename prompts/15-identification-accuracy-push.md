# Task 15 — Push first-pass identification toward ~100% correct

> **This is an umbrella prompt for a multi-session push.** It is a plan +
> findings + a staged worklist. Stage 0 (the measurement harness) is
> mandatory and comes first — every later stage is judged against it. Each
> lettered stage after that is its own commit, and can be its own session;
> start a new chat, say "follow `prompts/15-identification-accuracy-push.md`,
> stage C" and re-read this file. Read `prompts/README.md` and `SPEC.md §5`,
> `§13` first.

## The goal, stated plainly

James wants every book **identified, labelled, named, and filed correctly on
the very first scan** — as close to 100% as achievable — without adding a
manual review step to the happy path. Today the pipeline is good but has
measurable blind spots (below). The bet: **grounded AI + richer deterministic
evidence + real cross-checks + a thin safety net** gets first-pass accuracy
from "usually right" to "wrong maybe 1 in 100, and caught within a day".

## Current pipeline (per new file, `scan_service._process_file`)

1. `classify_file` → structural `multi_parent` / `no_parent`.
2. Calibre convert (`mobi`/`rtf`/`txt` → epub) in place.
3. sha256 dedup (`_find_primary_by_sha256`).
4. `parse_epub_safely` → `EpubEvidence(title, authors, language, description,
   isbn10, isbn13, series, series_number, text_snippet)`.
5. `score_quality`.
6. `find_rule_match` (author/series alias) → short-circuit, confidence 100.
7. `review_service.recent_corrections` (prompt 14 C) — AI-path few-shot.
8. `candidate_service.generate_candidates` → Google Books + Open Library,
   ISBN-first then title+author.
9. `identification_service.identify`:
   - **fast path**: an ISBN match between evidence and a candidate with
     `titles_match` + `texts_match(author)` → deterministic result (+ one
     `identify_series` AI call if no source has a series).
   - **AI path**: `_build_prompt(filename, evidence, candidates, corrections)`
     → forced `identify_book` tool.
10. `clamp_series_number` (drops `>50` / `<=0`).
11. `resolve_book` → fuzzy author (word-set), fuzzy series (word-set), strict
    title → `Book` row.
12. Routing on `computed_confidence`: `>=85` → `inbox` (auto-organize pass
    picks it up), `<85` → `review`.
13. `organize_service.build_target_path` → `Author/Series/` folder,
    `"Author, Title, Series, N.epub"`.

---

## Findings — where first-pass identification leaks

### F1. Metadata providers never return a series, and are otherwise thin

- `GoogleBooksProvider._to_candidate` ignores `volumeInfo.seriesInfo` (it
  exists in the API response), `categories` (BISAC genre), `subtitle`,
  `publisher`, `pageCount`. `publishedDate` is captured into
  `first_published` but nothing reads it.
- `OpenLibraryProvider._doc_to_candidate` ignores `series` (present on search
  docs), `subject`, and never follows up to `/works/{id}.json` or
  `/books/{id}.json` (richer, has `series`).
- **Net effect:** `MetadataCandidate.series` is *always* `None`.
  Therefore `confidence_service._series_in_a_source` can only be satisfied by
  the EPUB's own Calibre metadata, so `UNCORROBORATED_SERIES_PENALTY (-15)`
  fires on essentially every AI-supplied series — it is noise, not signal.
  And the fast-path "borrow series from a candidate" branch
  (`identification_service.identify`, the `candidate_with_series` lookup) is
  dead code.
- Only two providers; no third source to break a 1-vs-1 tie.

### F2. Deterministic evidence extraction leaves a lot unused

- `_extract_text_snippet` returns **only the first spine document**, capped at
  4000 chars. The first spine item is very often `cover.xhtml` /
  `titlepage.xhtml` / a nav doc — near-useless. The **copyright page**
  (publisher, "first published", printing line, ISBN) is usually spine item
  2–4; real first-chapter prose (a strong fingerprint) is later still.
- `EpubEvidence.description` **is parsed but never passed to `_build_prompt`**
  or to `confidence_service`.
- `_classify_identifier` only reads `<dc:identifier>` element text. Misses:
  ISBNs in `opf:scheme`/`opf:identifier` attributes, `<dc:source>`, multiple
  identifiers, ASIN, Goodreads/Google/OCLC IDs. Never collects `<dc:date>`,
  `<dc:publisher>`, `<dc:subject>` (BISAC/genre), `calibre:title_sort`.
- **No filename parsing.** Tracker / libgen / Anna's-Archive / Calibre
  filenames are information-dense ("Sanderson, Brandon - Mistborn 01 - The
  Final Empire (2006).epub"). Today the raw string is dropped into the prompt
  with zero structure and never becomes a candidate.

### F3. The AI identify call is single-shot, ungrounded, stale-prone

- One `identify_book` call, no self-consistency, no verification pass.
- **No web grounding.** The model recalls bibliographic facts from training
  data — precisely the "Scion (2026 standalone) → invented as Hierarchy #2"
  failure. Anthropic's `web_search` server tool would let it check
  title/author/series/pub-year against the live web. **This is the single
  biggest lever.**
- The prompt never states today's date or that recent books may be past the
  model's knowledge.
- The prompt omits `description`, publisher, pub date, and any structured
  filename parse.
- `identify_book`'s `title` and `author` are required non-null strings — there
  is **no abstain path**. A model that genuinely can't tell must still guess,
  and that guess auto-organizes if the deterministic components happen to add
  up (ISBN present + filename substring + complete EPUB metadata = 60+ before
  the model is even consulted).
- `needs_human_review: true` from the model, when computed confidence is
  otherwise borderline, is collected and then ignored.

### F4. Confidence scoring blind spots

- Series can't be corroborated (F1) → the penalty is nearly always-on.
- No positive credit for: filename agreeing with the *resolved* title/author
  (only with the EPUB title, via a weak substring test — `"It"` matches
  almost any filename); `description` corroborating; a plausible pub-year;
  a valid ISBN checksum.
- No penalty for **placeholder / junk metadata**: title `"Unknown"` /
  `"Calibre"` / `"book1"` / all-caps / `<=2` chars / exactly equal to the
  filename stem and nothing else; author `"Unknown"` / `"Anonymous"` /
  `"Various"` / a publisher name / `"Calibre"`.
- The **fast path trusts an ISBN match completely**. EPUB ISBNs are often
  wrong (wrong edition, the print ISBN on an ebook, an OCR'd digit). One
  wrong-but-valid ISBN whose provider title happens to fuzzy-match → wrong
  book at deterministic 90+, auto-organized, no AI sanity check.

### F5. `resolve_book` / naming isn't canonicalising

- Author match is exact word-set: "J.R.R. Tolkien" / "J. R. R. Tolkien" /
  "John Ronald Reuel Tolkien" / "Tolkien, J.R.R." → four different word sets
  → four `Author` rows. No initials/spacing normalisation, no "Last, First"
  handling. `Author.sort_name` exists in the schema and is **never
  populated**.
- `_find_or_create_series` uses `normalize_words` which **keeps articles**, so
  "The Stormlight Archive" vs "Stormlight Archive" fork on first pass (the
  Library Audit catches it *after*, prompt 1's series-merge fixes it *after* —
  but the goal here is not forking in the first place).
- `SeriesAlias` table exists and is **completely unused** — a ready-made
  canonicalisation mechanism sitting idle.
- Nothing canonicalises *case*: a lowercase EPUB title (`"the way of kings"`)
  is stored and written into the filename as-is.

### F6. No confirmation loop, no batch priors

- Everything `>=85` auto-organizes with zero human eyes; the only net is the
  opt-in bulk reident audit run manually later.
- 30 files landing at once from one author/series is a strong prior that is
  never used — each file is identified in complete isolation.

---

## The plan

Staged. **Stage 0 first, always.** Tiers reflect expected accuracy payoff;
within a tier, order is flexible. Ship each stage green (`cd backend &&
pytest`, `cd frontend && npm run build`) with its own commit + push, and tick
it off in this file + `prompts/README.md` + ROADMAP.

---

### Stage 0 — Ground-truth harness (MANDATORY, do first)

**Why.** "Close to 100%" is meaningless without a number. Every stage below
must show it moved the number, or it doesn't ship.

**Goal.**
1. New `backend/tests/identification_corpus/` — a set of ~60–150 real books
   James has hand-verified. Each entry: the recorded `EpubEvidence` (JSON),
   the recorded provider candidate JSON, **optionally** a recorded AI
   response, and the **known-correct** `{title, author, series,
   series_number}`.
   - Seed it: pick books across the hard cases — standalones the AI wants to
     seriesify, series with article variants, "Last, First" authors,
     omnibuses, foreign editions, Calibre-placeholder titles, tracker
     filenames, post-2024 books. Ask James to eyeball the answer key. Build a
     small script `scripts/snapshot_book.py <drive_file_id>` that pulls a
     real file's evidence+candidates into a corpus fixture (no AI call).
2. New `scripts/eval_identification.py` (and a `pytest -m corpus` marker):
   runs `identification_service.identify` (+ `resolve_book` normalisation)
   over the corpus with providers/AI mocked from the recordings, and reports
   **per-field precision** (title / author / series-name / series-number
   exact-after-normalise) plus a confusion list (what it got, what was
   right). A `--live` flag hits real providers + real AI for a spot check
   (costs credits — off by default, never in CI).
3. Record the **baseline numbers** in the commit message and a new
   `IDENTIFICATION-EVAL.md` at repo root. Every later stage appends its
   before/after to that file.

**Gotchas.**
- The corpus holds third-party bibliographic data, not book text — fine to
  commit. Keep `text_snippet` short (≤500 chars) in fixtures.
- Mock the AI via the existing `_FakeAIClient` pattern
  (`tests/test_identification_service.py`) — extend it to replay a recorded
  tool response per evidence hash.
- Don't gate CI on an absolute score (the corpus will grow); gate on
  "no per-field regression vs the number in `IDENTIFICATION-EVAL.md`".

**Acceptance.** `pytest -m corpus` runs offline, prints a per-field score
table, and fails if any field regresses below the recorded baseline.
`IDENTIFICATION-EVAL.md` exists with the baseline.

---

### Tier 1 — biggest accuracy wins

#### Stage A — Web-search grounding for the identify call

**Why.** F3. Directly kills the post-cutoff-book failure mode.

**What exists.** `AnthropicIdentificationClient.identify(prompt)` does one
`messages.create` with `tools=[IDENTIFY_BOOK_TOOL]`,
`tool_choice={"type":"tool","name":"identify_book"}`. `IdentificationService`
calls it only on the AI path (not fast path).

**Goal.**
- **Load the `claude-api` skill first** — get the current `web_search` server
  tool block, pricing, and the correct multi-tool / `tool_choice` pattern
  (you cannot force `identify_book` *and* let it search in one turn — it's
  `tool_choice:"auto"` + a small agentic loop, or a two-call
  search-then-identify).
- Add web search to the identify turn, capped (`max_uses` ~3). Prompt: give
  it **today's date** (from the server), tell it the evidence may describe a
  book published after its training cutoff, and instruct it to search to
  verify title / author / series membership / first-publication year before
  answering — *especially* before asserting a series.
- Only on the AI path, and ideally only when evidence is thin or the book
  looks recent (see Stage E's date signal) — a `should_ground(evidence,
  candidates)` gate so a clean multi-provider match doesn't pay for search.
- Store the search queries + result titles into `raw_response` for the review
  UI ("verified against: …").
- **Measure**: run Stage 0 `--live` on the recent-books slice before/after;
  record cost per identify and the accuracy delta.

**Gotchas.**
- `web_search` is billed per search — the Stage 0 `--live` run and
  `config.ai_identify_cost_usd` both need updating.
- Keep the non-grounded path working (network off / tool disabled by a new
  `settings.ai_web_search_enabled`, default true) — tests must not hit the
  network.
- A refusal or an empty search must fall back to answering from evidence, not
  error.

**Acceptance.** Recent/post-cutoff corpus slice accuracy up; non-grounded
tests unchanged; cost delta recorded in `IDENTIFICATION-EVAL.md`.

#### Stage B — Real series + richer candidates from providers

**Why.** F1. Makes series corroboratable and the uncorroborated-series
penalty meaningful; lights up dead fast-path code.

**What exists.** `providers/metadata/google_books.py`,
`open_library.py`, `types.py::MetadataCandidate` (already has `series` /
`series_number` fields, unused).

**Goal.**
- Google Books: populate `series` / `series_number` from
  `volumeInfo.seriesInfo.bookDisplayNumber` + the series title (it's nested;
  confirm the shape against a live response for a known series book).
  Populate `MetadataCandidate` genre from `categories`.
- Open Library: populate `series` from the search-doc `series` array;
  for ISBN hits, follow `/books/{id}.json` → `works` → `/works/{id}.json` for
  `series`. Keep it one extra request, cached per run.
- Feed the new fields into `_build_prompt` (candidate lines already print
  `series=`; add genre + pub year) and confirm
  `confidence_service.score` now actually credits `PROVIDERS_AGREE` on series
  and stops mis-firing `UNCORROBORATED_SERIES_PENALTY` when a provider backs
  the series.
- Re-check the fast-path `candidate_with_series` branch works end to end.

**Gotchas.**
- Provider series strings are messy ("Mistborn", "Mistborn (0)", "Mistborn
  Series") — normalise with `normalize_words` before comparison, same as
  everywhere else.
- Don't let a provider's *wrong* series now create a false
  `SERIES_DISAGREEMENT_PENALTY` against a correct EPUB series — test both
  directions.
- respx fixtures in `tests/` need the new JSON shapes.

**Acceptance.** Corpus series-name + series-number precision up; a test
proving a provider-backed series no longer takes the uncorroborated penalty.

#### Stage C — Structured filename parsing → an independent candidate

**Why.** F2. Tracker/Calibre filenames are often the *best* signal and are
currently unstructured.

**What exists.** `library-viewer/src/lib/parseFilename.ts` parses **only
BookBrain's own** `"Author, Title, Series, N"` output — not inbound files.
Nothing on the backend.

**Goal.**
- New `backend/app/providers/filename/parser.py` →
  `parse_book_filename(name) -> FilenameGuess(title, author, series,
  series_number, year, confidence)`. Handle the common real patterns:
  - `Author - Title.epub`
  - `Author - Series NN - Title.epub` / `Author - Title (Series NN).epub`
  - `Title - Author.epub`
  - `Last, First - Title.epub`
  - `Title (Year).epub`, trailing `(Z-Library)` / `(libgen)` / site tags
    stripped.
  - Calibre `Title - Author_NNNN.epub` (the `_NNNN` is a Calibre id, **not** a
    series number — this bit BookBrain before, see the Alexis Carew #301
    memory).
- Feed it into `_build_prompt` as a labelled `Filename parse:` block, **and**
  append it to `candidate_rows` in `scan_service._process_file` as a
  `BookCandidate(source="filename")` so it joins provider consensus and the
  confidence maths.
- New confidence component: `FILENAME_CANDIDATE_AGREES` when the filename
  guess matches the resolved title+author (replaces the weak substring
  `filename_matches_title`, or supplements it).

**Gotchas.**
- Be conservative — a low-confidence parse must not outvote a real provider.
  `FilenameGuess.confidence` gates whether it becomes a candidate at all.
- Don't reintroduce the Calibre-id-as-series-number bug — `metadata_sanity`
  clamp is the backstop but the parser should never emit it.
- Unit-test against a fixture list of ~40 real filenames from James's library
  (ask him to paste a sample of the messier inbox names).

**Acceptance.** Filename-parse unit tests green; corpus accuracy up on the
"tracker filename, thin EPUB metadata" slice; the weak substring test is gone
or demoted.

#### Stage D — Copyright-page + real-prose text extraction

**Why.** F2. The current snippet is usually the cover page.

**What exists.** `providers/epub/parser.py::_extract_text_snippet` — returns
the first existing spine doc, `[:4000]`.

**Goal.**
- Walk more of the spine. Skip items whose manifest `properties` include
  `nav`/`cover-image`, or whose stripped text is `<200` chars (cover/title
  pages). Concatenate the first ~2 substantive docs **plus** one doc from
  ~15–25% into the spine (first-chapter prose). Total cap ~4000 chars,
  labelled so the prompt can say which is which
  (`Copyright/front-matter text:` + `Body sample:`).
- Also parse and expose on `EpubEvidence`: `publisher` (`<dc:publisher>`),
  `pub_date` (`<dc:date>`), `subjects` (`list[str]` from `<dc:subject>`).
- Widen `_classify_identifier`: also scan `<dc:source>`, `opf:scheme`
  attributes, and collect **all** ISBNs found (not just the last one wins) —
  `EpubEvidence.all_isbns: list[str]`. Keep `isbn13`/`isbn10` as the
  best-single for back-compat.
- Put `description`, `publisher`, `pub_date`, `subjects` into `_build_prompt`.

**Gotchas.**
- Stay inside the existing `SafeZipReader` limits + the 10s parse timeout —
  reading 3 docs instead of 1 is fine, reading 300 isn't.
- `EpubEvidence` is used widely (`hash_evidence`, `score_quality`,
  `confidence_service`, reident's `evidence_from_sources`) — add fields with
  defaults, and update `evidence_from_sources` /
  `_evidence_to_metadata_sources` round-trip + its fidelity test.
- Don't change `hash_evidence`'s existing keys (it's the AI-decision cache
  key) unless you deliberately want to invalidate the cache — if you add the
  new fields to the hash, note it in the commit like prompt 14 C did.

**Acceptance.** Snippet quality visibly better on 5 spot-checked files;
corpus accuracy up on the "no ISBN, generic title" slice; evidence
round-trip test green.

---

### Tier 2 — cross-checks and scoring

#### Stage E — Junk / placeholder metadata detector

**Why.** F4. A junk-titled file must never fast-path and should never
auto-organize on EPUB metadata alone.

**Goal.**
- `metadata_sanity.py`: `looks_like_placeholder_title(s)` /
  `looks_like_placeholder_author(s)` — `"unknown"`, `"calibre"`, `"epub"`,
  `"untitled"`, `"anonymous"`, `"various"`, `"none"`, all-digits, `<=2`
  alnum chars, a bare `"book N"` / `"volume N"`, a known-publisher name for
  an author.
- Wire in:
  - `IdentificationService.identify` fast path: if the EPUB title/author is a
    placeholder, **skip the fast path** (force the AI path) even on an ISBN
    match.
  - `confidence_service.score`: new `PLACEHOLDER_METADATA_PENALTY` when the
    *resolved* title/author still looks like a placeholder.
  - Also: `TITLE_IS_FILENAME_ONLY_PENALTY` when the resolved title equals the
    filename stem and no provider/AI corroborates it.

**Gotchas.** Real titles like "It", "V.", "S." exist — keep the `<=2 chars`
rule paired with "and no ISBN and no provider match", not standalone.

**Acceptance.** Corpus "Calibre placeholder" slice: none auto-organize;
precision on that slice up.

#### Stage F — ISBN trust check (stop blind fast-pathing)

**Why.** F4. A wrong ISBN currently yields a wrong book at deterministic
confidence.

**Goal.**
- In `_find_isbn_match` / the fast path: require the ISBN-matched candidate's
  title to agree with the EPUB title within a real edit-distance
  (`text_match` — add a Levenshtein-ratio helper), not just `titles_match`'s
  normalise-and-equals. On disagreement, **do not** return a deterministic
  result — fall through to the AI path with the candidate still supplied.
- Validate every ISBN via `isbnlib.is_isbn13` / `is_isbn10` at parse time;
  drop invalid ones (don't feed a bad checksum to providers).
- If the EPUB has `all_isbns` (Stage D) and they disagree with each other,
  that's a signal — mild penalty, and prefer the one a provider confirms.

**Gotchas.** Legit case: an omnibus EPUB carrying one volume's ISBN. The
edit-distance check handles it (titles won't match → AI path → AI sorts it
out). Don't over-tighten and lose the genuine fast-path wins — measure fast
path hit-rate before/after in `IDENTIFICATION-EVAL.md`.

**Acceptance.** A test: wrong-ISBN-but-valid-checksum with a mismatched
provider title → AI path, not deterministic. Fast-path hit-rate on the corpus
doesn't collapse (>, say, 80% of its previous value).

#### Stage G — Confidence: positive corroboration components

**Why.** F4. Reward the signals that actually predict correctness.

**Goal.** Add to `confidence_service.score` (keep the sum-to-100 shape by
rebalancing, and update every existing test's expected numbers deliberately,
noting the reasoning):
- `FILENAME_CORROBORATES` (resolved title+author matches the Stage C guess).
- `DESCRIPTION_CORROBORATES` (EPUB `description` mentions the resolved
  title/author/series, or a provider description does).
- `PUBYEAR_PLAUSIBLE` (EPUB/provider pub year is a real year ≤ current, and
  they agree within a few years).
- Thread `resolved_series` through `reident_audit_service._recompute_confidence`
  too (the ROADMAP item) so historical + fresh scoring match.

**Gotchas.** Re-tune `confidence_auto_flagged` / `confidence_auto_organize`
against the corpus after rebalancing — the goal is *fewer* wrong
auto-organizes without a flood of false review-queue entries. Put the
chosen thresholds + the precision/recall at each in `IDENTIFICATION-EVAL.md`.

**Acceptance.** Corpus: auto-organize precision up, review-queue volume not
materially worse. Documented threshold sweep.

#### Stage H — Verification pass for the uncertain band

**Why.** F3. A second look at exactly the books most likely to be wrong.

**Goal.**
- When the AI path produces `70 <= computed_confidence < confidence_auto_organize`,
  make one **verification** call: a new `verify_identification` tool — "here
  is a proposed identification and the evidence; confirm it exactly, or
  correct it, and say which fields you're unsure of". Use web_search here too.
- Agree → keep, and +credit (it's now double-checked). Disagree → take the
  correction but force `review` (two AI opinions differed — a human should
  see it). Cost-gated by a new `settings.ai_verify_enabled`.
- This replaces naive self-consistency (cheaper, more targeted).

**Gotchas.** Don't loop — exactly one verification call. The verifier must
not just rubber-stamp: prompt it adversarially ("what would make this wrong?").

**Acceptance.** Corpus mid-band precision up; a test for the
agree/disagree/route-to-review branches.

---

### Tier 3 — process & safety net

#### Stage I — "Recently auto-organized" tray + optional soft-hold

**Why.** F6. Makes *effective* accuracy ~100% — a human glance within a day
catches the rare miss — without adding a step to the happy path.

**Goal.**
- Dashboard panel + `GET /api/library/recently-organized?since=48h` listing
  every file auto-organized in the window with its confidence, evidence
  summary, and one-click **Confirm** / **Correct** (reuse
  `CorrectFileForm` / `file_service.correct_file`).
- Optional `settings.organize_hold_hours` (default `0` = current behaviour):
  when `>0`, an auto-eligible file waits in `inbox` that long before the
  organize pass moves it, so a correction in the tray lands before any Drive
  move. Surface the pending count on the Dashboard.
- "Confirm" writes a lightweight positive signal (a `Review(status=approved)`
  or an `ai_decisions` flag) so Stage 0's corpus can grow from real
  confirmations.

**Gotchas.** The hold must not stall the nightly job indefinitely — held
files just aren't eligible yet; they flow next run.

**Acceptance.** Panel works against a running app (James verifies); hold=0
changes nothing; hold>0 delays the move and a tray correction pre-empts it.

#### Stage J — Author canonicalisation

**Why.** F5. Stop forking authors/series on the first pass.

**Goal.**
- `text_match`: `normalize_person_name` — handle "Last, First" → "First
  Last", collapse initials (`"J.R.R."` / `"J. R. R."` / `"J R R"` →
  `"jrr"`), so `_find_or_create_author` matches across those forms. Keep the
  *display* name as first-seen (or the most-complete seen).
- Populate `Author.sort_name` on create ("Last, First" form).
- `_find_or_create_series`: strip a leading article for the *match* (not the
  display), and **consult `SeriesAlias`** (finally use the table) — plus have
  `series_merge` write a `SeriesAlias` on apply (the other ROADMAP item),
  closing the loop so a re-fork can't happen.
- One-off `scripts/backfill_author_sort_names.py` + a repair that merges
  already-forked authors the new normaliser would now unify (mirror
  `title_merge_repair_service`).

**Gotchas.** Two genuinely different authors can share initials ("J. Smith").
Only merge on `normalize_person_name` equality *and* a shared book/ISBN or an
existing alias — be at least as conservative as `resolve_book`'s current
word-set match. Test "J.R.R. Tolkien" unifies; "James Smith" vs "Jane Smith"
stays split.

**Acceptance.** Corpus author precision up; repair script dry-run output
sane on the real DB; no new false author merges in tests.

#### Stage K — Batch priors

**Why.** F6. A pile of one author's books is a strong prior for the
stragglers.

**Goal.** After a scan batch finishes identifying, before the auto-organize
pass: if `>=3` files resolved with high confidence to the same author (or
series), re-score the *low-confidence* files in the same batch whose filename
/ Drive sibling names point at that author/series — a small confidence bump
if the AI's guess for them is consistent with the batch consensus; a nudge
into `review` (not auto-organize) if it *conflicts*.

**Gotchas.** Purely additive re-scoring — never silently rewrite an
identification. Keep it explainable (log why each straggler moved).

**Acceptance.** A test: 4 Discworld files + 1 thin one with "Discworld" in
the filename → the thin one's confidence reflects the batch. No effect when
there's no batch consensus.

---

## Sequencing

```
0  (harness)  ── ~done: harness + scripts + 74-book corpus landed
│               (IDENTIFICATION-EVAL.md); baseline number pending James's
│               answer-key verification, then `eval_identification.py --write-baseline`
│
├─ A  web-search grounding      ┐
├─ B  provider series           │ Tier 1 — do all three, any order
├─ C  filename parser           │
├─ D  richer text/evidence      ┘
│
├─ E  placeholder detector      ┐
├─ F  ISBN trust check          │ Tier 2 — E/F/G before H
├─ G  positive confidence       │
├─ H  verification pass         ┘
│
├─ I  recently-organized tray   ┐
├─ J  author canonicalisation   │ Tier 3 — independent
└─ K  batch priors              ┘
```

Re-run `pytest -m corpus` after **every** stage and paste the delta into
`IDENTIFICATION-EVAL.md`. A stage that doesn't move a number (or moves it
down) doesn't merge — reconsider it.

## When each stage lands

- Tick it in this file's Sequencing block and in `prompts/README.md`.
- Append before/after numbers to `IDENTIFICATION-EVAL.md`.
- Update ROADMAP.
- Update the memories `project_bookbrain_ai_series_hallucination` and
  `project_bookbrain_review_followups`.

## When the whole push is done

- `IDENTIFICATION-EVAL.md` shows the cumulative first-pass accuracy gain.
- Fold anything that became dead code (old `filename_matches_title`, the
  never-used `SeriesAlias` note, etc.) out.
- Write a short "identification pipeline, 2026" section in `SPEC.md` or
  `README.md` describing the final shape, since §5 will be well out of date.
