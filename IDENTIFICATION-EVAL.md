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
