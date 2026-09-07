# Task 21 — `find_rule_match` must not trust an unverified title at confidence 100 (REVIEW-2026-09-08 F3)

Read `prompts/README.md` for shared context. Small, self-contained.

## Why

`sticky_resolution.find_rule_match` (`backend/app/services/sticky_resolution.py:95`)
short-circuits the whole identification pipeline when a `library_rules`
author/series alias matches — returning:

```python
title=evidence.title or filename,   # <-- raw EPUB title, or the FILENAME
computed_confidence=100,
needs_human_review=False,
```

The rule only carries an author or series alias. The **title** is never
verified — it's whatever the EPUB's own metadata says (often a placeholder
like "Calibre" / "Unknown" / "book1"), or the filename stem if the EPUB has no
title. That auto-organises at confidence 100, skipping the Stage E placeholder
detector and all of `confidence_service`.

SPEC §2 says a rule "short-circuits exactly like a sticky correction" — but a
sticky correction carries a *human-verified* title; a rule doesn't.

Only 1 `library_rule` exists on the real DB today (a `series_alias`), so this
is rarely hit — but the design trusts a completely unvetted title, and rules
accumulate as James uses "apply to similar" on corrections.

## Goal

In `find_rule_match`, after resolving `matched_author` / `matched_series`:

- Compute the resolved title the same way it does now
  (`evidence.title or filename`).
- If `metadata_sanity.looks_like_placeholder_title(resolved_title,
  corroborated=bool(evidence.isbn13 or evidence.isbn10))` is true **or** the
  resolved title equals the filename stem with nothing else backing it:
  **do not short-circuit** — return `None` so the file flows through the
  normal candidate + AI path, with the alias still applied there (the alias
  rules are consulted again? confirm — if not, the AI path won't know about
  the alias; in that case keep the short-circuit but set
  `computed_confidence` to something below `confidence_auto_flagged` (85) and
  `needs_human_review=True` so it lands in the review queue with the alias
  applied, rather than auto-organising a junk title).
- A clean title + a matching alias keeps today's behaviour (confidence 100,
  auto-organise).

Pick whichever of the two branches is simpler given how the alias rules are
threaded — the acceptance criteria below hold either way.

## Gotchas

- `find_rule_match` is called *before* candidate generation and the AI call
  (`scan_service._process_file` ~line 557) — returning `None` means the file
  pays for the full pipeline, which is the correct trade for a junk title.
- The `series_number` on the rule path is `evidence.series_number` raw;
  `scan_service` applies `clamp_series_number` afterward on every path, so
  that's already covered — don't duplicate it.
- Don't break the happy path: a real title ("The Hobbit") + an author alias
  ("J.R.R. Tolkien" → "J. R. R. Tolkien") must still short-circuit at 100.

## Acceptance

- New test in `test_sticky_resolution.py`: a file with EPUB title `"Calibre"`
  + a matching author-alias rule does **not** get `computed_confidence=100` /
  `needs_human_review=False` — it either returns `None` or routes to review.
- Existing `find_rule_match` tests (clean title + alias → confidence 100)
  still pass.
- `cd backend && pytest` + `pytest -m corpus` green.

One commit. Note the SPEC §2 nuance in `ROADMAP.md`.
