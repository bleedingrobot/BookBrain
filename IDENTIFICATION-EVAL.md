# Identification eval

Ground-truth harness for **first-pass identification accuracy**
(`prompts/15-identification-accuracy-push.md`, Stage 0). Every later stage of
the push must show it moved a number here or it doesn't ship.

## The design: no human in the loop

The answer key for each corpus book is **triangulated from independent
sources**, not hand-verified. A field's ground truth is whatever **≥2
independent signals agree on** (after the same normalisation the harness scores
with):

| signal | independent of the pipeline? |
|---|---|
| **Wikidata** (P50 author, P179 series, P1545 ordinal) | yes — pipeline uses Google Books + Open Library |
| **web-search-grounded Claude, "identify from scratch"** | different job, live web, told today's date |
| **web-search-grounded Claude, "refute this proposed answer"** | adversarial framing |
| the EPUB's own embedded metadata | raw input, not identification output |
| a pipeline provider candidate (only if providers agree) | raw input |

Per-field **provenance**:
- `consensus` — ≥2 agree, at least one is *not* a Claude call → scored, headline number
- `weak` — ≥2 agree but all are Claude calls → scored only in the non-`--strict` view, flagged
- `unresolved` — signals disagree or too few had an opinion → **not scored** (an honest gap beats a wrong key)

Residual limitation, stated plainly: this can't catch a mistake that *every*
independent source also makes (rare — usually a genuinely contested fact like
"is *Urth of the New Sun* Book of the New Sun #5 or a standalone coda"). Those
land in `unresolved` and simply aren't scored. The structural invariants and
mutation tests below carry the "never confidently wrong" guarantee with no
ground truth at all.

## What runs

- `backend/tests/identification_corpus/*.json` — one fixture per real book:
  recorded EPUB evidence + provider candidates + recorded `identify_book`
  response + the triangulated `answer` block (with per-field `provenance`).
- `backend/tests/corpus_harness.py` — replays each fixture through
  `IdentificationService.identify` (providers + AI mocked from the recording)
  plus the real `resolve_book`, scores per-field precision over the fields that
  reached consensus.
- `backend/tests/test_identification_corpus.py` — the **`pytest -m corpus`**
  gate: fails if any per-field precision drops below the baseline below.
- `backend/tests/test_identification_invariants.py` — structural properties
  that need no ground truth (a standalone with a series number; a title that
  appears in no input; junk metadata reaching high confidence; non-determinism).
- `backend/tests/test_identification_mutation.py` — corrupt one input, assert
  the pipeline stays right *or* stops being confident — never a confident wrong
  auto-organise.
- `python scripts/eval_identification.py` — the human-facing score table +
  confusion list. `--strict` drops `weak` keys. `--live` re-runs against real
  providers + a real AI call (credits; never CI).
- `python scripts/build_truth.py --all --write` — (re)builds the triangulated
  answer keys. Source calls cached under
  `tests/identification_corpus/_truth_cache/`. Costs Anthropic credits.

## Adding a book

```
python scripts/snapshot_book.py from-db <file_id> --tag <case>   # rebuild from a past scan
python scripts/snapshot_book.py from-drive <drive_file_id> --with-ai
python scripts/build_truth.py --only <fixture-id> --write         # triangulate its answer
```

## Corpus composition

74 books, chosen for the failure modes in `prompts/15` §Findings —
`ai-series-uncorroborated`, `standalone-series-risk`, `fast-path-isbn`,
`fast-path-isbn-junk-series`, `omnibus`, `lastfirst-author`, `post-cutoff`,
`foreign-edition`, `messy-language-metadata`, `human-corrected`,
`clean-control`.

## Baseline

**59 of 74 books scored** (title/author ~59, series 47, series-# 43). ~48 books
have the full web-grounded Claude voices; the API ran out of credit before the
last ~15, which are Wikidata-only and mostly land `unresolved`. Re-running
`python scripts/build_truth.py --all --write` after a further top-up fills those
in (it resumes from `_truth_cache/`, ~$6 for the remainder).

What the harness already catches (the failure modes `prompts/15` targets):
- **invented series**: `christopher-rowe-sandstorm` — pipeline puts a standalone
  in "Forgotten Realms"; `cassandra-clare-the-course-of-true-love` — pipeline
  says "Tales from the Shadowhunter Academy", it's "The Bane Chronicles";
  `charles-stross-halo` — "Accelerando" vs "Macx Family".
- **missed series**: `dean-koontz-ashley-bell`, `carolyn-ives-gilman-testament-of-leaves`,
  `evolvedraccoon-retired-archmage` — pipeline says standalone, they're book 1 of something.
- **author canonicalisation** (Stage J): co-author lists (`Weis, Hickman` vs
  `Weis`), house pseudonyms (`Richard Awlinson` vs `Scott Ciencin`) — 2 of these
  auto-organise wrong today.
- **omnibus title padding**: `The Paladins Omnibus` vs `The Paladins`,
  `Wool Omnibus Edition (Wool 1 - 5)` vs `Wool Omnibus`.

<!-- eval-baseline:begin -->
```json
{
  "corpus_size": 74,
  "scored": 59,
  "generated": "2026-09-06",
  "include_weak": true,
  "coverage": {
    "title": 59,
    "author": 58,
    "series": 47,
    "series_number": 43
  },
  "precision": {
    "title": 0.9492,
    "author": 0.9483,
    "series": 0.8723,
    "series_number": 0.9535
  },
  "exact_match": 0.8136
}
```
<!-- eval-baseline:end -->

## Tier 3

### Stage J — author / series canonicalisation (`prompts/15` Stage J)

Finding F5: `_find_or_create_author` matched on a plain word set, so
"J.R.R. Tolkien" / "J. R. R. Tolkien" / "Tolkien, J.R.R." each forked a row;
`Author.sort_name` was never populated; `_find_or_create_series` kept a leading
article so "The Stormlight Archive" vs "Stormlight Archive" forked; the
`SeriesAlias` table was completely unused.

- **`text_match.normalize_person_name`** — the author match key: folds
  case/punctuation, reorders "Last, First", takes the first of a `&`/`;`/`and`
  co-author list, joins a run of initials (`J. R. R.` → `jrr`), drops a lone
  interior middle initial (`Iain M. Banks` → `iain banks` matches `Iain Banks`).
  Surname particles (`Le Guin, …`, `van …`) keep a two-word surname reading as
  Last-comma-First rather than a two-author list. `person_sort_name` derives
  the `sort_name` display form.
- `_find_or_create_author` matches on that key (display name kept verbatim —
  collaboration credits like "… & Gardner Dozois (editors)" vary too much to
  rewrite safely); `sort_name` populated on create and backfilled on match.
- `_find_or_create_series` matches ignoring a leading article, and **consults
  `SeriesAlias`** — and `apply_series_merge` now **writes** a `SeriesAlias` for
  every merged-away name, so a re-fork of a name already merged can't happen.
- `scripts/backfill_author_sort_names.py` (dry-run default) fills historical
  `sort_name`. `scripts/repair_forked_authors.py` (dry-run default) merges
  already-forked Author rows — but only a group that shares a book (same ISBN or
  strict-normalised title), so "J. Smith" ≠ "John Smith" stays split.

**Corpus author precision is flat at 94.8%.** The empty-DB harness creates a
fresh Author per book, so it can't show the *dedup* win (which is the whole
point — not forking on the live 2200-book library). A first cut that also
rewrote co-author display names to the primary author regressed author 94.8 →
93.1 (the triangulated keys disagree on whether a collaboration keeps both
names — "Weis, Hickman" → "Weis" but "Chaney, Maggert" → both), so display
rewriting was dropped. `wrong_auto_organized` back to 1; `pytest -m corpus`
green. No AI cost.

### Stage K — batch priors (`prompts/15` Stage K)

Finding F6: 30 files from one author landing in a single scan is a strong prior
that is never used — each file is identified in complete isolation.

- New `app/services/batch_prior_service.apply_batch_priors(session,
  drive_file_ids)`, called by `run_scan` after `_process_batch` and before the
  auto-organize pass.
- Consensus = ≥ 3 confidently-identified (`>= confidence_auto_flagged`,
  `status=inbox`) files in the batch sharing an author (`normalize_person_name`
  key) or a series (article-stripped word set).
- For a `review` file in the same batch whose *filename* names a consensus
  author/series:
  - identification **agrees** → `+12` confidence (capped at 92, never the ≥95
    tier), and if that clears 85 the file moves `review → inbox` and its pending
    `Review` is dropped;
  - identification **disagrees** → left in `review` with an explanatory note.
- Never touches title/author/series; every change is recorded under
  `ai_decisions.raw_response_json["batch_prior"]` (and re-running is a no-op).

Corpus is flat — the harness scores one file at a time, so a batch consensus
never forms. Unit-tested in `test_batch_prior_service.py` (the plan's 4-Discworld
+ 1-thin case, no-consensus, conflict, idempotency). No AI cost.

## Tier 3 (I remains)

J and K are shipped and deterministic (no AI cost). The corpus is structurally
blind to both — J's win is *dedup* on a populated library (the harness starts
empty), K's is a *batch* consensus (the harness scores one book at a time).
Stage I (recently-auto-organized tray + soft-hold) is frontend + API + a running
app to verify against — best as its own session.

## Per-stage log

| stage | date | title | author | series | series-# | exact | notes |
|---|---|---|---|---|---|---|---|
| 0 | 2026-09-06 | 94.9% (59) | 94.8% (58) | 87.2% (47) | 95.3% (43) | 81.4% | harness + triangulation + invariants + mutation. 59/74 scored (15 still Wikidata-only, credit ran out). This is the regression floor for Tier 1. |
| A | 2026-09-06 | 94.9% (59) | 94.8% (58) | 87.2% (47) | 95.3% (43) | 81.4% | web-search grounding on the AI identify turn, **recent-books-only gate (~3% of calls)** after a first cut grounded 95% and tripled per-identify cost. **Offline number unchanged by construction** — the corpus replays a frozen `identify_book` response, so grounding can only be measured live (see below). No regression; `pytest -m corpus` green. |
| B | 2026-09-06 | 94.9% (59) | 94.8% (58) | 87.2% (47) | 95.3% (43) | 81.4% | real provider series + genre from Google Books / Open Library; series-disagreement penalty now needs a **provider consensus**, not one lone provider, so a messy provider string can't tank a correct EPUB series. **Offline number unchanged by construction** — the 74 corpus fixtures were snapshotted before providers returned series, so every recorded candidate still has `series: null`; the effect is only visible live / on a re-snapshot. No regression; `pytest -m corpus` green. No AI-cost change (pure provider/HTTP work). |
| C | 2026-09-06 | 94.9% (59) | 94.8% (58) | 87.2% (47) | 95.3% (43) | 81.4% | structured inbound-filename parser (`providers/filename/parser.py`) → labelled prompt block + `FILENAME_MATCHES_TITLE` now driven by the parse agreeing with the *resolved* title/author (the old "title is a substring of the filename anywhere" test — `"It"` matched everything — demoted to a fallback for callers that don't parse the name). Per-field precision flat (frozen AI), but **`wrong_auto_organized` on the corpus dropped 2 → 1** — one book that got a spurious substring +5 and auto-organized wrong now scores under the bar. `pytest -m corpus` green, review-queue volume unchanged. No AI cost (deterministic). |
| D | 2026-09-06 | 94.9% (59) | 94.8% (58) | 87.2% (47) | 95.3% (43) | 81.4% | richer EPUB evidence: `_extract_text_snippet` walks the spine (skips cover/nav/titlepage + sub-200-char docs, takes 2 substantive front-matter docs + one body sample ~20% in, each labelled); new `EpubEvidence.publisher` / `pub_date` / `subjects` / `all_isbns` (scans every `<dc:identifier>` + `<dc:source>`); `description` + all four now in `_build_prompt`. `hash_evidence` deliberately **unchanged** — the ~2200 cached AI decisions stay valid, the richer evidence only reaches genuinely-new files. Per-field flat (frozen AI); `pytest -m corpus` green. No AI cost. |
| E | 2026-09-06 | 94.9% (59) | 94.8% (58) | 87.2% (47) | 95.3% (43) | 81.4% | placeholder/junk-metadata detector (`metadata_sanity.looks_like_placeholder_title` / `_author` — "Unknown"/"Calibre"/"book1"/bare-number/publisher-name-as-author; short titles need an ISBN or provider match). Fast path is **skipped** when the EPUB title/author is a placeholder (forces the AI path); `PLACEHOLDER_METADATA_PENALTY` (-30) and `TITLE_IS_FILENAME_ONLY_PENALTY` (-10) fire on the *resolved* metadata. No corpus case exercises it (the 59 scored books all have real metadata) so per-field + `wrong_auto_organized` are flat; `pytest -m corpus` green. No AI cost. |
| F | 2026-09-06 | 94.9% (59) | 94.8% (58) | 87.2% (47) | 95.3% (43) | 81.4% | ISBN-trust check: the fast path now also requires `text_match.title_similarity` (difflib ratio on the *strict*-normalised titles) ≥ 0.80 between the EPUB title and the ISBN-matched provider title — `titles_match` alone strips everything after a `:` so "Mistborn: The Final Empire" and "Mistborn: The Well of Ascension" passed it. On failure it falls through to the AI path (candidate still supplied). **Fast-path hit-rate on the corpus unchanged at 25.4%** — no legit deterministic win lost. Per-field flat; `pytest -m corpus` green. No AI cost. |
| G | 2026-09-06 | 94.9% (59) | 94.8% (58) | 87.2% (47) | 95.3% (43) | 81.4% | positive corroboration components: `DESCRIPTION_CORROBORATES` (+3, a blurb mentions the resolved title/author/series) and `PUBYEAR_PLAUSIBLE` (+2, a real ≤-current year present and sources within 5y). Additive, opt-in via `resolved_title`/`resolved_author`, so reident + old callers unchanged. `resolved_series`/`_title`/`_author` now threaded through `reident_audit_service._recompute_confidence` so the display recompute matches a fresh scan. **Thresholds kept at 85/95** — see sweep below. Corpus flat (first cut at +5/+3 tipped one series-only-wrong book over 85 → dialed to +3/+2, which crosses no threshold in the corpus); `pytest -m corpus` green. No AI cost. |
| K | 2026-09-07 | 94.9% (59) | 94.8% (58) | 87.2% (47) | 95.3% (43) | 81.4% | batch priors: `batch_prior_service.apply_batch_priors` runs between the scan batch and auto-organize — a ≥3-file author/series consensus lifts a `review` file in the same batch whose filename names it (+12, cap 92, drops the pending Review if it clears 85); disagreement is logged, not acted on. Corpus flat (harness scores one file at a time — no batch). No AI cost. |
| J | 2026-09-07 | 94.9% (59) | 94.8% (58) | 87.2% (47) | 95.3% (43) | 81.4% | author canonicalisation: `normalize_person_name` match key (initials/`Last, First`/co-author) + `Author.sort_name` populated + `_find_or_create_series` article-insensitive + `SeriesAlias` consulted/written on merge. Corpus flat — the empty-DB harness can't show the dedup win; display-name rewriting was tried and dropped (regressed author 94.8→93.1 against inconsistent collaboration keys). Repair + backfill scripts, dry-run default. No AI cost. |
| H | 2026-09-06 | 94.9% (59) | 94.8% (58) | 87.2% (47) | 95.3% (43) | 81.4% | verification pass for the uncertain band (`70 ≤ computed_confidence < 95`): one adversarial `audit_book_identity` call — agree → +10 confidence (capped 94, so it still shows in the audit log), disagree → take the correction **and** force review (two AI opinions differed), uncertain → force review. **`settings.ai_verify_enabled` defaults OFF** (one extra ~$0.03 call per uncertain new book; James is budget-limited), so the corpus doesn't exercise it and the number is flat. Branch tests in `test_identification_service.py`. |

### Stage A — web-search grounding (`prompts/15` Stage A)

`AnthropicIdentificationClient.identify(prompt, ground=True)` runs the identify
turn with the `web_search_20260209` server tool + a system prompt that states
today's date and tells the model to *verify* title / author / series / first-pub
year before answering (especially before asserting a series). `tool_choice` is
`auto` (you can't force `identify_book` and allow search in one turn); if the
model answers in text instead of committing, a second forced call pins the
structured answer using the searched-up context. Refusal / exhausted-loop →
falls back to the plain forced call. Search queries + result titles land in
`raw_response["grounding"]` for the review UI.

Gated per-call by `identification_service.should_ground(...)`: web search is
billed per call, so it fires **only when there's a recent-year signal** (a year
within ~2 of today in the filename or a provider `first_published`) — the
post-training-cutoff "invented series" risk. That is ~3% of the corpus (vs 95%
under the first, too-broad gate — thin/conflicting providers no longer trigger
it; they just get a normal un-grounded call and, if the score is low, the review
queue). Toggle with `settings.ai_web_search_enabled` (default on),
`ai_web_search_max_uses` = 2. `config.ai_identify_cost_usd` 0.03 → 0.035 (the
blended figure — most calls don't ground). The `web_search` tool variant is
picked per model (`_web_search_tool`): `_20260209` for Opus/Sonnet, basic
`_20250305` for Haiku 4.5; if the model rejects it entirely the grounded call
falls back to a plain un-grounded identify rather than erroring.

**Live measurement — still to run** (needs Anthropic credit; offline CI never
hits the network):

```
cd backend && python scripts/eval_identification.py --live --tag post-cutoff
cd backend && python scripts/eval_identification.py --live --tag standalone-series-risk
```

Compare per-field series precision + the confusion list on those slices against
the Stage 0 baseline, and record cost/identify here.

**Known gap left for later:** the fast-path `identify_series` lookup (fired when
ISBN+provider+EPUB agree on title/author but no source has a series) is *not*
grounded — that path is the other half of the "Scion → invented Hierarchy #2"
mechanism. Stage B (real provider series) shrinks how often it runs; grounding
it too is a candidate follow-up once Stage B lands.

### Stage B — real provider series + richer candidates (`prompts/15` Stage B)

Finding F1 said `MetadataCandidate.series` was *always* `None` — both providers
ignored the series data the APIs return. Fixed:

- **Open Library.** Search docs: read the `series` array (asking for it via
  `fields=`), split a trailing `#N` / `, N` / `(Book N)` off the name
  (`types.split_series_and_number`). ISBN path (`jscmd=data`): parse
  `series:<name>` / `genre:<name>` out of the `subjects` list, and — one extra,
  per-edition-cached GET to `/books/OL…M.json` — read its clean `series` field
  when the subjects don't carry one. Follow-up failure is silent.
- **Google Books.** `seriesInfo.bookDisplayNumber` (or `volumeSeries[0]
  .orderNumber`) → `series_number`; the series *name* (which `seriesInfo` never
  carries) is taken from a numbered trailing parenthetical on the title —
  `"The Final Empire (Mistborn, #1)"` — and only when it has a number, so
  `"(Movie Tie-in Edition)"` is not mistaken for a series. A bare number with no
  trustworthy name is dropped. `categories` → `genre` (BISAC leaf).
- **`MetadataCandidate.genre`** added; `_build_prompt` candidate lines now print
  `series=`, `genre=` and `published=`.
- **Confidence.** `_series_in_a_source` already credited a provider-backed
  series, so `UNCORROBORATED_SERIES_PENALTY` now correctly stands down once a
  provider confirms the AI's series (it never could before). The
  `SERIES_DISAGREEMENT_PENALTY` was retuned: with real (messy, sometimes wrong)
  provider series now flowing in, it fires **only on a provider consensus** —
  the EPUB series matches no candidate *and* ≥2 candidates agree on a different
  one. A single lone provider disagreeing with the EPUB's own series no longer
  penalises. Tested both directions (`test_confidence_service.py`,
  `test_identification_service.py`).
- Dead code revived: the fast-path `candidate_with_series` branch
  (`IdentificationService.identify`) now actually fires when a provider supplies
  the series — covered end to end by
  `test_fast_path_skips_series_lookup_when_a_provider_already_has_it`.

**Offline corpus number is unchanged** and that is expected: all 74 fixtures
were captured before this change, so their recorded candidates still have
`series: null` and the harness replays them verbatim. Real movement needs a
re-snapshot (`scripts/snapshot_book.py from-drive … ` — free, no AI) or a
`--live` run. No regression; `pytest -m corpus` green.

### Stage C — structured inbound-filename parser (`prompts/15` Stage C)

Finding F2: tracker / libgen / Anna's-Archive / Calibre filenames are
information-dense but the raw string was dropped into the prompt unstructured
and never became a candidate.

- New `backend/app/providers/filename/parser.py` —
  `parse_book_filename(name) -> FilenameGuess(title, author, series,
  series_number, year, confidence)`. Deterministic, no I/O. Handles
  `Author - Title`, `Author - Series NN - Title`, `Title - Author`,
  `Last, First - Title`, `Title (Series NN)`, enclosed `(Year)`, all-lowercase
  torrent names, and strips `(Z-Library)` / `[libgen…]` / release-group tags.
  `FilenameGuess.confidence` (0..1) gates use — `usable` at ≥ 0.5, which needs
  title + author or title + a numbered series; a bare title doesn't clear it.
- **Calibre-id guard**: a trailing `_1234` is Calibre's book id, stripped, never
  a series number; an absurd `(… #301)` (> 50) is dropped, not emitted — the
  "Alexis Carew #301" placeholder bug can't recur through this path.
- `identification_service.identify` parses the filename once, adds a labelled
  `Structured parse of the filename …` block to `_build_prompt`, and passes an
  explicit `filename_corroborates` verdict (structured parse agrees with the
  *resolved* title + author) to `confidence_service.score`.
- `confidence_service`: `FILENAME_MATCHES_TITLE` (still 5 pts) is now driven by
  that verdict when supplied; the old "resolved/EPUB title is a substring of the
  filename anywhere" test stays only as the fallback for
  `reident_audit_service._recompute_confidence` (which doesn't parse the name).
  `"It"` was a substring of almost every filename — that's the weak test the
  plan wanted gone.
- `scan_service._process_file` persists the guess as a
  `BookCandidate(source="filename")` for the re-identification audit; it is
  filtered back out of the provider-consensus / disagreement maths in
  `reident_audit_service` and `snapshot_book.py` (a heuristic parse is not a
  provider).

**Per-field precision is flat** (the harness replays a frozen `identify_book`
answer, so a prompt hint / confidence tweak can't move title/author/series).
What *did* move: `wrong_auto_organized` on the corpus **2 → 1** — a book that
picked up a spurious substring +5 and auto-organized wrong now lands under the
bar. Review-queue volume unchanged. `pytest -m corpus` green; no AI cost.

### Stage D — copyright-page + real-prose text extraction (`prompts/15` Stage D)

Finding F2: `_extract_text_snippet` returned only the first spine document —
almost always `cover.xhtml` / `titlepage.xhtml`, near-useless — and `description`
was parsed but never sent to the model.

- **`providers/epub/parser.py::_extract_text_snippet`** now walks the spine
  (bounded to 10 reads for the parse timeout): skips docs whose manifest
  `properties` include `nav` / `cover-image`, whose basename looks like
  cover/titlepage/toc/halftitle, or whose stripped text is < 200 chars; takes
  the first 2 substantive docs as `[front matter]` (the copyright page —
  publisher, "first published", ISBN — lands here) plus one doc ~20% into the
  spine as `[body sample]` (first-chapter prose, a strong fingerprint), total
  still capped at 4000 chars. Falls back to the old first-doc behaviour when
  nothing substantive is found (very short books).
- **New `EpubEvidence` fields** (all defaulted): `publisher` (`<dc:publisher>`),
  `pub_date` (`<dc:date>`, preferring `opf:event="publication"`), `subjects`
  (`list[str]` from `<dc:subject>`), `all_isbns` (`list[str]` — every valid ISBN
  across all `<dc:identifier>` **and** `<dc:source>`, deduped, document order).
  `isbn13` / `isbn10` are now the first of each length from `all_isbns` (was
  "last `<dc:identifier>` wins").
- **`_build_prompt`** now includes `EPUB description`, `EPUB publisher`,
  `EPUB publication date`, `EPUB subjects/genre`, and the full ISBN list when
  there's more than one.
- **`hash_evidence` is deliberately unchanged** — it does not include the new
  fields (or `text_snippet`), so the ~2200 cached `ai_decisions` stay valid and
  are not re-billed. The richer evidence reaches only genuinely-new files.
- Round-trip: `scan_service._evidence_to_metadata_sources` /
  `reident_audit_service.evidence_from_sources` / `scripts/snapshot_book.py`
  updated for the new fields; fidelity test in `test_reident_audit_service.py`.

**Per-field corpus precision flat** (frozen AI). `pytest -m corpus` green; no AI
cost (deterministic parse).

### Stage E — placeholder / junk metadata detector (`prompts/15` Stage E)

`metadata_sanity.looks_like_placeholder_title` / `looks_like_placeholder_author`:
a curated stub list ("unknown", "calibre", "untitled", "book"/"novel"/"cover",
…), `book N` / `volume N` patterns, all-digits, and — only when *not*
corroborated by an ISBN or a provider/AI match — a ≤ 2-alnum-char string, so
real short titles ("It", "1984", "S.") survive with corroboration. A publisher
name in the author field ("Tor Books", "Penguin", …) counts as a placeholder
author.

- **Fast path**: `IdentificationService.identify` skips `_find_isbn_match`
  entirely when the EPUB title/author is a placeholder — the AI path runs and
  the candidate is still passed to it.
- **Confidence**: `PLACEHOLDER_METADATA_PENALTY` (-30) when the *resolved*
  title/author still looks like a stub; `TITLE_IS_FILENAME_ONLY_PENALTY` (-10)
  when the resolved title is exactly the filename stem and nothing (provider or
  AI) says so. Both opt-in via new `resolved_title` / `resolved_author` params;
  `reident_audit_service` and existing tests pass neither, so they're unchanged.

No scored corpus book has junk metadata (the triangulation needs real
bibliographic data to produce an answer key), so the corpus number is flat —
but the invariant/mutation tests already cover "junk metadata must not reach
high confidence".

### Stage F — ISBN trust check (`prompts/15` Stage F)

`_find_isbn_match` trusted an ISBN with only `titles_match` behind it, and
`titles_match` strips everything after a `:` — so a wrong-but-valid EPUB ISBN
pointing at another volume of the same series ("Mistborn: The Well of Ascension"
where the book is "Mistborn: The Final Empire") passed and auto-organised as the
wrong book.

- New `text_match.title_similarity(a, b) -> float` — difflib ratio on the
  *strict*-normalised titles (leading article folded, subtitle kept).
- The fast path now also requires `title_similarity ≥ 0.80`; below that it
  falls through to the AI path with the candidate still supplied.
- ISBN checksum validation was already done at parse time
  (`_classify_identifier` / `_collect_isbns`).

**Fast-path hit-rate on the corpus: 25.4% before and after** — the stricter
check cost no legitimate deterministic win. (The corpus's genuine ISBN matches
all have real title agreement; a synthetic wrong-ISBN case is unit-tested in
`test_identification_service.py`.)

### Stage G — positive corroboration components (`prompts/15` Stage G)

Two additive bonuses in `confidence_service.score`, credited only when the
caller passes the resolved identification (identification_service does; reident
and old callers don't):

- `DESCRIPTION_CORROBORATES` (+3) — an EPUB or provider blurb contains the
  resolved title, author, or series (normalised substring).
- `PUBYEAR_PLAUSIBLE` (+2) — a real year ≤ the current year is present in the
  EPUB `pub_date` or a provider `first_published`, and all such years agree
  within 5.

`FILENAME_CORROBORATES` already landed in Stage C (`FILENAME_MATCHES_TITLE`
driven by the structured `filename_corroborates` verdict).
`reident_audit_service._recompute_confidence` now threads `resolved_series` /
`resolved_title` / `resolved_author` (the ROADMAP item), so the audit's display
recompute equals what a fresh scan of that book scores today.

**Threshold sweep** (offline corpus, 59 scored — hard-case-weighted and
frozen-AI, so not representative of the real library's distribution, but the
only data available):

| `confidence_auto_flagged` | auto-organised | of which correct | precision | held for review |
|---|---|---|---|---|
| 75 | 20 | 18 | 0.90 | 39 |
| 80 | 15 | 13 | 0.87 | 44 |
| **85 (current)** | **6** | **5** | **0.83** | **53** |
| 88 | 4 | 4 | 1.00 | 55 |
| 90 | 1 | 1 | 1.00 | 58 |

Raising the bar to 88 would buy precision on this slice but the slice is not
representative — on the real ~2200-book library most books have an ISBN and a
clean provider match and legitimately auto-organise. **Kept at 85/95.** The
+3/+2 bonuses are small enough that they cross no threshold in the corpus (a
first cut at +5/+3 tipped one series-only-wrong book over 85 and was dialed
back). Corpus numbers unchanged; `pytest -m corpus` green.

### Stage H — verification pass for the uncertain band (`prompts/15` Stage H)

When the AI path lands at `70 ≤ computed_confidence < confidence_auto_organize`,
`IdentificationService.identify` makes **one** adversarial follow-up call
(`_build_verification_prompt` → the existing `audit_book_identity` tool, reused
— it already has the "confirm exactly or correct it, series especially" shape):

- `stored_is_correct` (and the series checks out) → `+10` confidence, capped at
  94 so a verified book still appears in the auto-organize audit log rather than
  sailing through silently at ≥95.
- `stored_is_wrong` → take the `corrected_*` fields **and** force the review
  queue — two AI opinions differed, a human should see it.
- `uncertain` → keep the original but force review.
- The verifier failing (refusal / error) → keep the original, no change.

**`settings.ai_verify_enabled` defaults `False`.** It is one extra ~$0.03 model
call per uncertain *new* book (`settings.ai_verify_cost_usd`), and steady-state
spend is the constraint here — turn it on only if the review queue is noisier
than the budget allows. Off by default means the offline corpus never calls it
and the number is unmoved; the agree / disagree / uncertain / failure branches
are unit-tested with `AI_VERIFY_ENABLED=true` and a fake client.

Not grounded (no `web_search` on the verify call) — a deliberate cost choice;
adding grounding here is a candidate follow-up if the verifier itself proves
stale-prone.

---

## Tier 2 summary

E–H are shipped. E/F/G are deterministic (no AI cost); H adds an opt-in call
that is off by default. The offline corpus per-field numbers are unchanged
across all four — the frozen `identify_book` reply caps what a scoring / routing
change can demonstrate offline (same limitation noted for Tier 1 A–D). The
measurable offline movement in the whole push so far remains Stage C's
`wrong_auto_organized` 2 → 1. Real gains from E–H (junk metadata caught, wrong
ISBNs not fast-pathed, borderline-correct books lifted, uncertain-band books
double-checked) need a `--live` run or a re-snapshot — both free of AI cost for
E/F/G, and H needs `ai_verify_enabled` on for its slice.
