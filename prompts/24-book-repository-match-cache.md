# Task 24 — Batch-scoped author/series match cache (REVIEW-2026-09-08 F6)

Read `prompts/README.md`. Performance; no behaviour change. No rush — fine at
today's size, worth doing before the library doubles.

## Why

`book_repository._find_or_create_author` and `_find_or_create_series`
(`backend/app/services/book_repository.py:110`, `142`) each run
`select(Author)` / `select(Series)` — **every** row (446 authors, 590 series
today) — materialise them, and loop in Python to find a match. Once per file.
And it all happens inside `get_book_write_lock()`, which serialises the entire
scan pipeline's DB phase across every file.

A post-`Clear Library` rebuild is ~2,200 files × (~446 + ~590) row loads, run
strictly serially. It grows quadratically with the library.

## Goal

A match cache scoped to a single scan/rebuild batch (not process-global — it
must not go stale across runs):

- New `book_repository.MatchCache` (or a plain dict passed through): maps
  `normalize_person_name(name)` → `author_id` and `_series_match_key(name)` →
  `series_id`, populated once at batch start from a single
  `select(Author.id, Author.name)` / `select(Series.id, Series.name)`, and
  updated in-memory whenever `_find_or_create_*` creates a new row.
- `resolve_book` takes an optional `cache` param; `scan_service._process_batch`
  builds one and threads it through every `_process_file` →
  `identification`/`resolve_book` call for that batch.
- On a cache **miss**, fall back to the current full-scan match (a row created
  by a concurrent path within the same batch but not yet in the cache) — so
  correctness never depends on the cache, only speed.
- `review_service.correct` / `file_service.correct_file` /
  `sticky_resolution` call `resolve_book` **without** a cache (one-off, no
  batch) — the param defaults to `None` and those paths are unchanged.

Alternative if the cache threading is too invasive: add a normalised
`match_key` column to `authors` / `series` with a unique index, populated on
write, and query by it directly (`WHERE match_key = ?`). This needs a
migration + a backfill script but removes the full scan entirely. Pick
whichever is cleaner after reading `scan_service._process_batch`.

## Gotchas

- The write lock exists because two files in one batch can each decide a
  not-yet-seen author doesn't exist and both create it — the cache must be
  updated *under the same lock*, right after `session.flush()` of a new row,
  or the race reopens.
- `_find_or_create_author`'s `sort_name` backfill-on-match (Stage J) must
  still happen — a cache hit returns an id, so either the cache stores enough
  to skip the row load, or a hit still does one `session.get(Author, id)` to
  set `sort_name` (still O(1) vs O(rows)).
- `pytest -m corpus` must stay green — the harness exercises `resolve_book`.
- Benchmark: time a `pytest tests/test_scan_service.py` run before/after, and
  note it in the commit.

## Acceptance

- `_find_or_create_author` / `_find_or_create_series` issue **one** bulk query
  per batch, not one per file (assert via a query counter or a spy in a new
  `test_book_repository.py` case).
- The concurrent-create race test (two files, same new author, one batch →
  one `Author` row) still passes.
- `cd backend && pytest` + `pytest -m corpus` green.

One commit.
