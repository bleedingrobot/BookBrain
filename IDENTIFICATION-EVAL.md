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
| A | 2026-09-06 | 94.9% (59) | 94.8% (58) | 87.2% (47) | 95.3% (43) | 81.4% | web-search grounding on the AI identify turn. **Offline number unchanged by construction** — the corpus replays a frozen `identify_book` response, so grounding can only be measured live (see below). No regression; `pytest -m corpus` green. |

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

Gated per-call by `identification_service.should_ground(...)`: skipped only for
the safe case (no recent-year signal **and** ≥2 providers corroborate each other
**and** an ISBN is present); everything thinner or newer grounds. Toggle with
`settings.ai_web_search_enabled` (default on). `config.ai_identify_cost_usd`
padded 0.03 → 0.06 (web_search ≈ $0.01/search × up to 3, plus the larger prompt).

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
