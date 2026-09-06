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

**Partial — 35 of 74 books scored.** The Anthropic API ran out of credit
partway through the first `build_truth` run, so only ~5 books have the
web-search-grounded Claude voices; the rest are triangulated from Wikidata +
EPUB + provider only, which rarely confirms a *series* independently (series
coverage is 9/35). Top up API credit and re-run
`python scripts/build_truth.py --all --write` (~$8, ~40 min) to fill in the
grounded voices — coverage should jump to ~55-65/74 and the baseline can be
re-stamped. The number below is the regression floor until then.

Standing findings the harness already surfaces (all Stage J / author
canonicalisation): `Iain M. Banks` vs `Iain Banks`, co-author lists
(`Weis, Hickman` vs `Weis`), and house pseudonyms (`Richard Awlinson` vs
`Scott Ciencin`) — 4 of the 4 current misses, 2 of them auto-organising.

<!-- eval-baseline:begin -->
```json
{
  "corpus_size": 74,
  "scored": 35,
  "generated": "2026-09-06",
  "include_weak": true,
  "coverage": {
    "title": 35,
    "author": 33,
    "series": 9,
    "series_number": 9
  },
  "precision": {
    "title": 1.0,
    "author": 0.8788,
    "series": 1.0,
    "series_number": 1.0
  },
  "exact_match": 0.8857
}
```
<!-- eval-baseline:end -->

## Per-stage log

| stage | date | title | author | series | series-# | exact | notes |
|---|---|---|---|---|---|---|---|
| 0 | 2026-09-06 | 100% (35) | 87.9% (33) | 100% (9) | 100% (9) | 88.6% | harness + triangulation + invariants + mutation landed. Partial baseline — API credit exhausted mid-`build_truth`; re-run to complete. |
