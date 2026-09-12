# identification corpus

Hand-verified real books for the first-pass identification eval
(`prompts/15-identification-accuracy-push.md`, Stage 0). See
`../../../IDENTIFICATION-EVAL.md` for the whole picture.

One `<id>.json` per book:

```jsonc
{
  "id": "brandon-sanderson-the-final-empire",
  "case_tags": ["fast-path-isbn"],       // which failure mode this probes
  "notes": "…",
  "source": { "file_id": 1234, "drive_file_id": "…", "filename": "…",
              "captured_from": "db", "stored_model": "…", "stored_confidence": 80 },
  "filename": "Sanderson, Brandon - Mistborn 01 - The Final Empire.epub",  // inbound name
  "evidence":   { … EpubEvidence fields, text_snippet capped at 500 chars … },
  "candidates": [ { … MetadataCandidate fields … } ],
  "candidate_fidelity": "fresh" | "db-reconstructed" | "db-reconstructed+isbn-refit",
  "recorded_ai":        { … the identify_book tool `input`, or null if fast/rule path … },
  "recorded_series_ai": { … the identify_series tool `input`, or null … },
  "answer": {
    "title": "The Final Empire", "author": "Brandon Sanderson",
    "series": "Mistborn", "series_number": 1,
    "verified": true,
    "source": "triangulated",            // scripts/build_truth.py
    "provenance": {                       // per field: how sure we are
      "title": "consensus", "author": "consensus",
      "series": "weak", "series_number": "unresolved"
    }
  },
  "truth_claims": [ … each source's raw opinion … ],
  "truth_disagreements": [ "series: wikidata=... web_claude_identify=..." ]
}
```

The `answer` block is **triangulated, not hand-verified** — see
`../../../IDENTIFICATION-EVAL.md`. `provenance` drives scoring: `consensus`
fields count toward the headline number, `weak` (Claude-only agreement) count
only in the non-`--strict` view, `unresolved` fields are not scored at all.
An `answer` block with no `provenance` key (pre-triangulation fixtures) is
treated as all-`consensus`.

## Fidelity notes

- `db-reconstructed` candidates come from stored `book_candidates` rows, which
  don't keep ISBNs — so the ISBN fast path can't fire for them unless
  `candidate_fidelity` is `…+isbn-refit` (only done when the stored model was
  `deterministic`, i.e. we know the fast path really did fire). For full
  fidelity on a fast-path book, recapture it with
  `snapshot_book.py from-drive <drive_file_id>`.
- `recorded_ai: null` + an AI-path identification ⇒ the entry is
  `skipped_offline` in `pytest -m corpus` (nothing to replay). Recapture with
  `from-drive … --with-ai` to make it scoreable offline.
- The recorded AI answer is **frozen**. Stages that change the prompt or add web
  grounding (A, H) only move the number under
  `eval_identification.py --live --tag post-cutoff`.

## Adding / fixing entries

```
python scripts/snapshot_book.py from-db 1234 --tag fast-path-isbn --note "…"
python scripts/snapshot_book.py from-spec tests/identification_corpus/_corpus_spec.json  # batch
python scripts/snapshot_book.py from-drive <drive_file_id> --with-ai
```

Files here starting with `_` (like `_corpus_spec.json`) are tooling inputs, not
fixtures — `load_corpus()` skips them.

Then triangulate its answer key (no manual step):

```
python scripts/build_truth.py --only <fixture-id> --write
```

Source lookups are cached under `_truth_cache/` (also `_`-prefixed, skipped by
`load_corpus()`). `--refresh` ignores the cache.
