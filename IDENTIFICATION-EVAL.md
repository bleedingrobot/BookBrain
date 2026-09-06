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

## Per-stage log

| stage | date | title | author | series | series-# | exact | notes |
|---|---|---|---|---|---|---|---|
| 0 | 2026-09-06 | 94.9% (59) | 94.8% (58) | 87.2% (47) | 95.3% (43) | 81.4% | harness + triangulation + invariants + mutation. 59/74 scored (15 still Wikidata-only, credit ran out). This is the regression floor for Tier 1. |
| A | 2026-09-06 | 94.9% (59) | 94.8% (58) | 87.2% (47) | 95.3% (43) | 81.4% | web-search grounding on the AI identify turn, **recent-books-only gate (~3% of calls)** after a first cut grounded 95% and tripled per-identify cost. **Offline number unchanged by construction** — the corpus replays a frozen `identify_book` response, so grounding can only be measured live (see below). No regression; `pytest -m corpus` green. |
| B | 2026-09-06 | 94.9% (59) | 94.8% (58) | 87.2% (47) | 95.3% (43) | 81.4% | real provider series + genre from Google Books / Open Library; series-disagreement penalty now needs a **provider consensus**, not one lone provider, so a messy provider string can't tank a correct EPUB series. **Offline number unchanged by construction** — the 74 corpus fixtures were snapshotted before providers returned series, so every recorded candidate still has `series: null`; the effect is only visible live / on a re-snapshot. No regression; `pytest -m corpus` green. No AI-cost change (pure provider/HTTP work). |
| C | 2026-09-06 | 94.9% (59) | 94.8% (58) | 87.2% (47) | 95.3% (43) | 81.4% | structured inbound-filename parser (`providers/filename/parser.py`) → labelled prompt block + `FILENAME_MATCHES_TITLE` now driven by the parse agreeing with the *resolved* title/author (the old "title is a substring of the filename anywhere" test — `"It"` matched everything — demoted to a fallback for callers that don't parse the name). Per-field precision flat (frozen AI), but **`wrong_auto_organized` on the corpus dropped 2 → 1** — one book that got a spurious substring +5 and auto-organized wrong now scores under the bar. `pytest -m corpus` green, review-queue volume unchanged. No AI cost (deterministic). |
| D | 2026-09-06 | 94.9% (59) | 94.8% (58) | 87.2% (47) | 95.3% (43) | 81.4% | richer EPUB evidence: `_extract_text_snippet` walks the spine (skips cover/nav/titlepage + sub-200-char docs, takes 2 substantive front-matter docs + one body sample ~20% in, each labelled); new `EpubEvidence.publisher` / `pub_date` / `subjects` / `all_isbns` (scans every `<dc:identifier>` + `<dc:source>`); `description` + all four now in `_build_prompt`. `hash_evidence` deliberately **unchanged** — the ~2200 cached AI decisions stay valid, the richer evidence only reaches genuinely-new files. Per-field flat (frozen AI); `pytest -m corpus` green. No AI cost. |
| E | 2026-09-06 | 94.9% (59) | 94.8% (58) | 87.2% (47) | 95.3% (43) | 81.4% | placeholder/junk-metadata detector (`metadata_sanity.looks_like_placeholder_title` / `_author` — "Unknown"/"Calibre"/"book1"/bare-number/publisher-name-as-author; short titles need an ISBN or provider match). Fast path is **skipped** when the EPUB title/author is a placeholder (forces the AI path); `PLACEHOLDER_METADATA_PENALTY` (-30) and `TITLE_IS_FILENAME_ONLY_PENALTY` (-10) fire on the *resolved* metadata. No corpus case exercises it (the 59 scored books all have real metadata) so per-field + `wrong_auto_organized` are flat; `pytest -m corpus` green. No AI cost. |

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
