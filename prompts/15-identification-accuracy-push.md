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

**UPDATE (2026-09-06): Stage 0 shipped, redesigned to need no human step.**
James did not want to hand-verify answer keys, so the ground truth is
*triangulated*: `scripts/build_truth.py` asks Wikidata + two web-search-grounded
Claude calls (identify + adversarial-refute), and a field's answer is only
accepted when >=2 independent signals agree (>=1 non-Claude ⇒ `consensus`,
Claude-only ⇒ `weak`, else `unresolved`/not scored). Added
`test_identification_invariants.py` (standalone-with-a-number, invented title,
junk→high-confidence, non-determinism) and `test_identification_mutation.py`
(corrupt one input ⇒ stays right or stops being confident) — both need no
ground truth and carry the "never confidently wrong" guarantee. See
`IDENTIFICATION-EVAL.md`.

---

### Tier 1 — biggest accuracy wins

#### Stage A — Web-search grounding for the identify call

> **DONE 2026-09-06.** `AnthropicIdentificationClient.identify(prompt, ground=)`
> — `ground=True` runs the identify turn with the `web_search_20260209` server
> tool + `tool_choice:auto` + a system prompt giving today's date and telling
> the model to verify title/author/series/pub-year before answering; a text
> answer triggers one forced follow-up; refusal / exhausted loop falls back to
> the plain forced call; queries + result titles stored in
> `raw_response["grounding"]`. Per-call gate `identification_service.should_ground`
> — **recent-year signal only** (filename or provider pub date within ~2 years),
> ~3% of calls; a first cut that also grounded on thin/conflicting providers hit
> 95% and tripled per-identify cost, so it was pulled back to the post-cutoff
> risk it actually addresses. `settings.ai_web_search_enabled` (default true),
> `ai_web_search_max_uses` (2). `ai_identify_cost_usd` 0.03 → 0.035. Offline corpus is unchanged by construction
> (frozen `identify_book` reply); live measurement on the recent/standalone
> slices is still to run — see `IDENTIFICATION-EVAL.md` § Stage A.

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

> **DONE 2026-09-06.** `MetadataCandidate.genre` added;
> `GoogleBooksProvider` / `OpenLibraryProvider` now populate `series` /
> `series_number` / `genre`. Google Books: number from
> `seriesInfo.bookDisplayNumber`, name from a *numbered* trailing title
> parenthetical only, genre from `categories`. Open Library: search-doc
> `series` array + `jscmd=data` `subjects` (`series:` / `genre:` prefixes) +
> one per-edition-cached follow-up GET to `/books/OL…M.json` for its clean
> `series` field (silent on failure). Shared `types.split_series_and_number`
> peels a trailing `#N` off a name. `_build_prompt` candidate lines print
> `series=` / `genre=` / `published=`. `SERIES_DISAGREEMENT_PENALTY` retuned to
> require a **provider consensus** (≥2 candidates agree on a different series)
> so a lone messy provider string can't contradict a correct EPUB series —
> both directions tested. Offline corpus number flat by construction (fixtures
> predate provider series); no regression, `pytest -m corpus` green; no AI-cost
> change. See `IDENTIFICATION-EVAL.md` § Stage B.

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

> **DONE 2026-09-06.** New `backend/app/providers/filename/parser.py` —
> `parse_book_filename(name) -> FilenameGuess(title, author, series,
> series_number, year, confidence)`, deterministic, no I/O. Handles
> `Author - Title` / `Author - Series NN - Title` / `Title - Author` /
> `Last, First - Title` / `Title (Series NN)` / enclosed `(Year)` /
> all-lowercase names, strips site/release tags; a trailing Calibre `_1234` is
> never a series number and an absurd `(… #301)` is dropped.
> `FilenameGuess.usable` at `confidence >= 0.5`. `identify()` parses once, adds a
> labelled `Structured parse of the filename …` block to `_build_prompt`, and
> passes an explicit `filename_corroborates` verdict to `confidence_service`;
> `FILENAME_MATCHES_TITLE` (5 pts) is now driven by that verdict, with the old
> substring test kept only as the fallback for
> `reident_audit_service._recompute_confidence`. `scan_service` persists the
> guess as `BookCandidate(source="filename")` (filtered back out of the
> provider-consensus maths in reident + snapshot_book). Per-field corpus
> precision flat (frozen AI); `wrong_auto_organized` 2 → 1. Tests:
> `test_filename_parser.py` (17), plus cases in `test_confidence_service.py` /
> `test_identification_service.py` / `test_scan_service.py`. `pytest -m corpus`
> green; no AI cost.

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

> **DONE 2026-09-06.** `_extract_text_snippet` walks the spine (bounded to 10
> reads): skips `nav`/`cover-image` properties, cover/titlepage/toc basenames,
> and < 200-char docs; emits `[front matter] …` (first 2 substantive docs) +
> `[body sample] …` (one doc ~20% into the spine), 4000-char cap, old first-doc
> fallback for tiny books. New `EpubEvidence.publisher` / `pub_date` /
> `subjects` / `all_isbns` (every ISBN across all `<dc:identifier>` + `<dc:source>`;
> `isbn13`/`isbn10` = first of each length). `_build_prompt` gained
> `EPUB description` / `publisher` / `publication date` / `subjects/genre` +
> the full ISBN list. **`hash_evidence` untouched** — new fields aren't in it,
> so the cached `ai_decisions` stay valid and unbilled; richer evidence reaches
> only new files. Round-trip (`_evidence_to_metadata_sources` /
> `evidence_from_sources` / `snapshot_book.py`) + fidelity test updated. Tests:
> `test_epub_parser.py` (+4, new `build_rich_epub` fixture), plus
> `test_reident_audit_service.py` / `test_identification_service.py`. Per-field
> corpus precision flat (frozen AI); `pytest -m corpus` green; no AI cost.

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

> **DONE 2026-09-06.** `metadata_sanity.looks_like_placeholder_title` /
> `looks_like_placeholder_author` (+ `has_placeholder_metadata`): curated stub
> list, `book N` / `volume N`, all-digits, and — only without ISBN/provider
> corroboration — ≤ 2 alnum chars (so "It"/"1984"/"S." survive with an ISBN).
> Publisher names count as a placeholder author. `IdentificationService.identify`
> skips `_find_isbn_match` when the EPUB title/author is a placeholder.
> `confidence_service`: `PLACEHOLDER_METADATA_PENALTY` -30 (resolved title/author
> still a stub) + `TITLE_IS_FILENAME_ONLY_PENALTY` -10 (resolved title == the
> filename stem, uncorroborated), both opt-in via `resolved_title`/
> `resolved_author` so reident + old callers unchanged. No scored corpus book
> has junk metadata (triangulation needs real data), so the number is flat; the
> invariant/mutation tests already carry "junk must not reach high confidence".

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

> **DONE 2026-09-06.** New `text_match.title_similarity(a, b)` — difflib ratio
> on the *strict*-normalised titles (article folded, subtitle kept).
> `_find_isbn_match` now also requires `title_similarity >= 0.80` between the
> EPUB title and the ISBN-matched provider title (catches "Mistborn: The Final
> Empire" vs "…The Well of Ascension", which `titles_match` collapses); on
> failure the AI path runs with the candidate still supplied. Parse-time ISBN
> checksum validation was already done in Stage D. Corpus fast-path hit-rate
> unchanged at 25.4% — no legit deterministic win lost.

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

> **DONE 2026-09-06.** `DESCRIPTION_CORROBORATES` (+3 — an EPUB/provider blurb
> contains the resolved title, author, or series) and `PUBYEAR_PLAUSIBLE` (+2 —
> a real ≤-current year in `pub_date`/`first_published`, all such years within
> 5). **Additive, not rebalanced** — small enough that the [0,100] clamp
> protects the top and they can't cross a threshold alone; opt-in via
> `resolved_title`/`resolved_author` so reident + old callers are unchanged (no
> mass test-number churn). `FILENAME_CORROBORATES` already landed in Stage C.
> `resolved_series`/`_title`/`_author` now threaded through
> `reident_audit_service._recompute_confidence` (the ROADMAP item).
> **Thresholds kept at 85/95** — the sweep in `IDENTIFICATION-EVAL.md` is
> hard-case-weighted + frozen-AI, can't justify a change. A first cut at +5/+3
> tipped one series-only-wrong book over 85; dialed back to +3/+2. Corpus flat.

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

> **DONE 2026-09-06.** For an AI-path result at `70 <= computed_confidence <
> confidence_auto_organize`, `IdentificationService.identify` makes ONE
> adversarial follow-up call. **Reused the existing `audit_book_identity` tool**
> (its "confirm exactly or correct it, series especially" shape already fits)
> via `_build_verification_prompt` rather than adding a new schema. Agree
> (+ series real) → `+10` confidence capped at 94 (still shows in the audit
> log); `stored_is_wrong` → take `corrected_*` **and** force review; `uncertain`
> → force review; verifier error → keep original. `settings.ai_verify_enabled`
> **defaults False** — one extra ~$0.03 call per uncertain new book, and
> steady-state spend is the constraint. Not grounded (cost). Branch tests with
> `AI_VERIFY_ENABLED=true` + a fake client. Corpus flat (feature off).

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

> **DONE 2026-09-07.** `GET /api/library/recently-organized?since=48h`
> (`recently_organized_service` + `schemas/recently_organized.py`): every file
> auto-organized in the window, newest first, deduped to one row per file (its
> latest move `Operation`), each with the confidence it moved at, a one-line
> `evidence_summary` (AI reasoning + `ISBN in file` / `N provider matches` /
> `web-search verified` / `batch` / `double-checked`), current status, and a
> `confirmed` flag. `since` accepts `24h`/`48h`/`7d`, clamped to 30d.
> **Confirm** = `POST /api/files/{id}/confirm` → `file_service.confirm_file`
> writes an idempotent `Review(status=approved)` row carrying the confirmed
> title/author/series (`proposed_json.confirmed=true`,
> `source="recently_organized_tray"`) — never moves the file, never an
> `Operation`; a future `build_truth.py` / `snapshot_book.py` mode can harvest
> these as free ground truth (hook noted, harvester not built).
> **Correct** reuses the existing `CorrectFileForm` → `/files/{id}/correct`.
> Soft-hold: `settings.organize_hold_hours` (`ORGANIZE_HOLD_HOURS`,
> `get_organize_hold_hours`, clamped [0, 720], **default 0 = today's exact
> behaviour**), surfaced on the Settings page folded into `OrganizeSettings`
> (the `/settings/organize` GET/PUT pair). When > 0,
> `organize_service.organize_eligible_files` adds a single
> `File.discovered_at <= now - hold` WHERE clause — a held file just isn't
> eligible yet and flows on the next pass, so the nightly job can't stall.
> `organize_service._organize_file` now also stamps `Operation.confidence` /
> `Operation.model` from the file's latest `AIDecision` (they were nullable and
> unset before). Frontend: `RecentlyOrganized.tsx` composed into the Dashboard,
> with a 24h/48h/7d toggle and (when the hold is on) a "held, waiting Nh"
> sub-list. Corpus flat by construction (the eval harness has no organize pass
> and no Dashboard). No AI cost anywhere. **Whole prompts/15 push complete.**

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

> **DONE 2026-09-07.** `text_match.normalize_person_name` (match key — folds
> case/punct, reorders "Last, First", first-of-co-author-list, joins an initials
> run `J. R. R.`→`jrr`, drops a lone interior middle initial `Iain M. Banks`→
> `Iain Banks`; surname particles keep "Le Guin, …" as Last-comma-First) +
> `person_sort_name`. `_find_or_create_author` matches on the key; **display
> name kept verbatim** — a first cut that rewrote co-author credits to the
> primary author regressed the corpus (the triangulated keys disagree on
> whether a collaboration keeps both names). `Author.sort_name` populated on
> create + backfilled on match. `_find_or_create_series` matches ignoring a
> leading article and **consults `SeriesAlias`**; `apply_series_merge` **writes**
> one per merged-away name. `scripts/backfill_author_sort_names.py` +
> `scripts/repair_forked_authors.py` (both dry-run default; the repair only
> merges a group that shares a book/ISBN). Corpus flat (empty-DB harness can't
> show dedup); `pytest -m corpus` green; no AI cost. **Not run against James's
> real DB** — he runs the two scripts' dry-runs and eyeballs.

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

> **DONE 2026-09-07.** New `app/services/batch_prior_service.apply_batch_priors`,
> called by `run_scan` after `_process_batch`, before the auto-organize pass.
> Consensus = ≥ 3 confident (`>= confidence_auto_flagged`, `status=inbox`) files
> in the batch sharing an author (`normalize_person_name` key) or series
> (article-stripped word set). A `review` file in the batch whose *filename*
> names a consensus author/series: **agrees** → `+12` (cap 92, never ≥95), moves
> `review → inbox` + drops the pending `Review` if it clears 85;
> **disagrees** → stays in review + a logged note. Never rewrites title/author/
> series; every change under `ai_decisions.raw_response_json["batch_prior"]`,
> re-run is a no-op. `test_batch_prior_service.py` (4-Discworld + thin, no
> consensus, conflict, idempotency). Corpus flat (harness scores one file at a
> time). No AI cost.

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
0  (harness)  ── DONE. 74-book corpus + IDENTIFICATION-EVAL.md. Answer keys
│               TRIANGULATED, not human-verified (James: zero manual steps):
│               a field's truth = what >=2 of {Wikidata, web-grounded Claude x2,
│               EPUB, provider} agree on; else "unresolved", not scored. Plus
│               invariant + mutation tests (no ground truth needed).
│               Baseline (2026-09-06, 59/74 scored): title 94.9% author 94.8%
│               series 87.2% series-# 95.3%. 15 books still Wikidata-only
│               (credit ran out) — `build_truth.py --all --write` after top-up
│               finishes them and re-stamps. Gate = `pytest -m corpus`.
│
├─ A  web-search grounding      ── DONE (2026-09-06). `identify(prompt, ground=)`
│                                  + `web_search_20260209` + `should_ground()` gate
│                                  (recent-year signal only, ~3% of calls, for cost)
│                                  + `settings.ai_web_search_enabled`. Offline corpus
│                                  unchanged by construction; live measurement pending
│                                  credit (see IDENTIFICATION-EVAL.md § Stage A).
├─ B  provider series           ── DONE (2026-09-06). Google Books + Open Library
│                                  now populate MetadataCandidate.series /
│                                  series_number / genre; series-disagreement
│                                  penalty needs a provider *consensus* now.
│                                  Offline corpus flat by construction (fixtures
│                                  predate provider series) — see IDENTIFICATION-EVAL.md.
├─ C  filename parser           ── DONE (2026-09-06). providers/filename/parser.py
│                                  → labelled prompt block + structured
│                                  filename_corroborates verdict replaces the
│                                  weak substring test; corpus wrong_auto_organized
│                                  2→1. Per-field flat (frozen AI).
├─ D  richer text/evidence      ── DONE (2026-09-06). Spine-walking text snippet
│                                  ([front matter] + [body sample], skips cover/nav);
│                                  EpubEvidence.publisher/pub_date/subjects/all_isbns;
│                                  description + all four into _build_prompt.
│                                  hash_evidence unchanged (cache stays valid).
│                                  Tier 1 COMPLETE.
│
├─ E  placeholder detector      ── DONE (2026-09-06). metadata_sanity placeholder
│                                  title/author detectors; fast path skipped on a
│                                  placeholder EPUB; PLACEHOLDER_METADATA_PENALTY -30
│                                  + TITLE_IS_FILENAME_ONLY_PENALTY -10 on resolved
│                                  metadata. No scored corpus case; per-field flat.
├─ F  ISBN trust check          ── DONE (2026-09-06). text_match.title_similarity
│                                  (difflib, strict-normalised); fast path needs
│                                  >= 0.80 title agreement w/ the ISBN-matched
│                                  candidate. Fast-path hit-rate 25.4% unchanged.
├─ G  positive confidence       ── DONE (2026-09-06). DESCRIPTION_CORROBORATES +3 /
│                                  PUBYEAR_PLAUSIBLE +2 (additive, opt-in);
│                                  resolved_series/_title/_author threaded through
│                                  reident recompute. Thresholds kept 85/95 (sweep
│                                  in IDENTIFICATION-EVAL.md). Corpus flat.
├─ H  verification pass         ── DONE (2026-09-06). One adversarial
│                                  audit_book_identity call for the 70–95 band;
│                                  agree +10 (cap 94), disagree take-correction +
│                                  force review, uncertain force review.
│                                  settings.ai_verify_enabled defaults OFF (cost).
│                                  Tier 2 COMPLETE.
│
├─ I  recently-organized tray   ── DONE (2026-09-07). GET /api/library/recently-
│                                  organized + Dashboard tray (Confirm/Correct) +
│                                  POST /api/files/{id}/confirm (idempotent
│                                  Review(approved) signal) + settings.organize_
│                                  hold_hours soft-hold (default 0 = no-op, a
│                                  single discovered_at WHERE clause). Operation.
│                                  confidence/model now populated by organize.
│                                  Corpus flat by construction. No AI cost.
│                                  WHOLE PUSH COMPLETE.
├─ J  author canonicalisation   ── DONE (2026-09-07). normalize_person_name match
│                                  key (initials / Last,First / co-author-first) +
│                                  Author.sort_name populated + series match
│                                  ignores a leading article + SeriesAlias
│                                  consulted & written on merge. Corpus flat
│                                  (empty-DB harness can't show dedup). Repair +
│                                  backfill scripts (dry-run default).
└─ K  batch priors              ── DONE (2026-09-07). batch_prior_service between
                                   the scan batch and auto-organize: a >=3-file
                                   author/series consensus lifts a review file
                                   in the same batch whose filename names it
                                   (+12, cap 92); disagreement logged. Never
                                   rewrites an id. Corpus flat (no batch in the
                                   harness).
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

## When the whole push is done — DONE 2026-09-07

- `IDENTIFICATION-EVAL.md` shows the cumulative first-pass accuracy gain
  (offline per-field flat by the frozen-AI construction; the measurable
  movement is Stage C's `wrong_auto_organized` 2 → 1 — see that file's summary).
- Dead-code sweep: nothing safely removable. The old substring
  `filename_matches_title` is still the live fallback for
  `reident_audit_service._recompute_confidence` (which doesn't parse filenames);
  `SeriesAlias` went from unused to load-bearing in Stage J. Left as-is.
- "Identification pipeline (2026)" section written in `SPEC.md` (after §5).
