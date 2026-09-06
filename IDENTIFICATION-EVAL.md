# Identification eval

Ground-truth harness for **first-pass identification accuracy**
(`prompts/15-identification-accuracy-push.md`, Stage 0). "Close to 100%" is
meaningless without a number — this is the number, and every later stage of the
push must show it moved the number or it doesn't ship.

## How it works

- `backend/tests/identification_corpus/*.json` — one fixture per hand-verified
  real book: the recorded EPUB evidence, the metadata-provider candidates, the
  recorded AI response (when the book took the AI path), and the
  **known-correct** `{title, author, series, series_number}`.
- `backend/tests/corpus_harness.py` — replays each fixture through
  `IdentificationService.identify` (providers + AI mocked from the recording)
  plus the real `resolve_book`, and scores per-field precision
  (exact-after-normalise) against the answer key.
- **Gate:** `cd backend && pytest -m corpus` — fails if any per-field precision
  drops below the baseline recorded below. The corpus grows over time, so this
  never asserts an absolute score, only "no regression vs the last number".
- **Human view:** `python scripts/eval_identification.py` prints the score
  table + a confusion list. `--live` re-runs against real providers + a real AI
  call for the recent-books slice (costs credits; never in CI).
- **Add a book:** `python scripts/snapshot_book.py from-db <file_id>` (rebuild
  from a past scan) or `... from-drive <drive_file_id> --with-ai` (capture a new
  file live). Then hand-verify the drafted `answer` block and set
  `"verified": true`.

## Corpus composition

74 books, chosen for the failure modes in `prompts/15` §Findings:

| case tag | what it probes |
|---|---|
| `human-corrected` | books James has already hand-corrected — gold answers |
| `ai-series-uncorroborated` | AI supplied a series no source backs (F1/F3) |
| `standalone-series-risk` | standalones the model may "seriesify" |
| `fast-path-isbn` | deterministic ISBN fast path (F4) |
| `fast-path-isbn-junk-series` | fast path passing an EPUB's junk `calibre:series` straight through |
| `omnibus` | multi-work volumes that shouldn't get a volume number |
| `lastfirst-author` | "Surname, First" filenames + `&`/`;` co-authors (F5) |
| `post-cutoff` / `foreign-edition` | books past the model's training data / non-English editions (F3, Stage A) |
| `messy-language-metadata` | wrong `<dc:language>` on an otherwise-fine book |
| `clean-control` | easy books that must stay at 100% |

## Baseline

<!-- eval-baseline:begin -->
_Not yet recorded._ The drafted answer keys are still mostly
`"verified": false` (auto-filled from the stored identification, which is
circular). Once James has eyeballed the answer keys, run
`python scripts/eval_identification.py --write-baseline` to stamp the real
Stage-0 numbers here. Until then `pytest -m corpus` skips the regression
assertion.
<!-- eval-baseline:end -->

## Per-stage log

| stage | date | title | author | series | series-# | exact | notes |
|---|---|---|---|---|---|---|---|
| 0 | — | — | — | — | — | — | harness landed; baseline pending answer-key verification |
