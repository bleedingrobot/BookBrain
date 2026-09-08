# Task 25 — Hardcover API integration

Read `prompts/README.md`. New external metadata source. **Phase 1 is the one
to build now**; Phases 2–3 are scoped here so the shape is agreed but are
separate later sessions.

## Why Hardcover

[Hardcover](https://hardcover.app) is a Goodreads-style book tracker with a
**human-librarian-curated catalogue** and a GraphQL API
(`https://api.hardcover.app/v1/graphql`, Hasura-style). It directly targets
BookBrain's weakest identification field — **series** (~87% precision, recurring
AI series hallucination, see [[project-bookbrain-ai-series-hallucination]]) —
because its `book_series` data has real positions, canonical de-duplication,
and compilation/partial filtering that Google Books and Open Library don't.

### Hard constraints (from their docs, 2026)

- **Backend only. Never from a browser.** "You can only access this API from
  localhost or APIs." The token must stay secret; CORS blocks it anyway.
  Client-side support + OAuth are on their 2026 roadmap but not shipped. →
  the `library-viewer` never calls Hardcover; everything routes through the
  backend and reaches the viewer via `bookbrain-index.json`.
- **Auth:** a Personal Access Token (`Authorization: Bearer …`) from the
  account settings page, with scopes and a required expiry. James creates it
  on a (free) Hardcover account; BookBrain stores it in `.env` like
  `ANTHROPIC_API_KEY` (a static secret, not a per-user OAuth token).
- **Rate limits (Free tier):** 5,000/day, 60/min, burst 10. One request may
  contain ≤5 top-level queries (or 1 `search`). A `search` counts as 1.
  Response carries `RateLimit` / `RateLimit-Policy` / `Retry-After` headers.
- **Beta, explicitly unstable:** "anything you build could break", "we may
  reset tokens without notice", GraphQL **max query depth 3 is on the
  roadmap** (our nested queries are depth 4 today — if that lands, the
  provider errors → returns `[]` → identification falls back cleanly).
- **Licence:** catalogue data (books/series/editions/authors/publishers/
  characters) is free to use — "we make no copyright or proprietary rights
  over this data". **User-owned data (reviews, ratings, lists, reading
  journals, goals) may NOT be used by a public-facing or commercial product**
  — BookBrain must only ever read catalogue data. Not for training publicly-
  available/commercial LLMs (feeding metadata into a Claude *identification*
  prompt is inference, not training — fine). If Hardcover cover images are
  ever displayed publicly, the site needs a DMCA takedown policy — but
  BookBrain already makes its own covers from the EPUBs, so don't use theirs.

### Posture

Treat Hardcover as **one more enrichment provider with graceful fallback**,
exactly like the Open Library fallback in the wishlist search. Never
load-bearing: if the token is missing/expired/rate-limited/the schema
changed, the provider returns `[]` and identification proceeds on Google
Books + Open Library + EPUB + AI as it does today.

---

## Phase 1 — `HardcoverProvider` (build now)

A third `BookMetadataProvider` alongside `GoogleBooksProvider` /
`OpenLibraryProvider`, feeding `candidate_service`. **No changes to
`confidence_service` or `identification_service`** — a Hardcover
`MetadataCandidate.series` flows into `candidates` and automatically:
- satisfies `confidence_service._series_in_a_source` → stands down
  `UNCORROBORATED_SERIES_PENALTY` when the AI's series matches Hardcover's;
- counts toward `PROVIDERS_AGREE` / the `SERIES_DISAGREEMENT` consensus.

That's the whole Phase-1 win: human-curated series corroboration, for free,
through the existing plumbing.

### Files

- **`app/core/config.py`** — `hardcover_api_token: str = ""`.
- **`backend/.env.example`** — `HARDCOVER_API_TOKEN=` with a one-line comment
  (where to get it: hardcover.app → account settings → Hardcover API → New API
  Key; set a long expiry; scope `read` is enough).
- **`app/providers/metadata/hardcover.py`** — `HardcoverProvider(BookMetadataProvider)`:
  - `__init__(self, token: str, client: httpx.AsyncClient | None = None)`.
  - `search_by_isbn(isbn)` → GraphQL POST:
    ```graphql
    query ($isbn: String!) {
      editions(
        where: {_or: [{isbn_13: {_eq: $isbn}}, {isbn_10: {_eq: $isbn}}]}
        order_by: {users_count: desc}
        limit: 3
      ) {
        isbn_13 isbn_10
        book {
          title subtitle description release_year
          contributions { author { name } }
          book_series(order_by: {featured: desc}, limit: 1) {
            position
            series { name }
          }
        }
      }
    }
    ```
    One `MetadataCandidate` per edition (dedupe identical book ids). `series`
    from `book_series[0].series.name`, `series_number` from
    `book_series[0].position`. `authors` from `contributions[].author.name`
    (all of them — the co-author policy filters later).
  - `search_by_title_author(title, author)` → the `search` query
    (`query_type: "Book"`, `per_page: 5`), parse `results.hits[].document`:
    `title`, `subtitle`, `author_names[]`, `featured_series` /
    `featured_series_position` / `series_names[]`, `isbns[]`, `release_year`,
    `description`. Build up to 5 candidates. `search` server timeout is 2 s —
    use a ~8 s client timeout.
  - **Rate limiting, instance-level** (NOT a module singleton — avoids the
    `conftest._reset_shared_singletons` lock-landmine; the real
    `_scan_service` holds one provider instance for the process, which is
    where limiting matters). A small async token bucket: ~0.9 req/s refill,
    burst 8 (stays under 60/min + 10 burst even with `_SCAN_CONCURRENCY = 6`
    files each making one call). Lazily create the `asyncio.Lock` on first
    async use so it binds to the running loop.
  - On HTTP 429: read `Retry-After`, `asyncio.sleep` once, retry once, then
    give up → `[]`.
  - On any other error, a GraphQL `errors` array, missing token, or an
    unexpected shape → `[]` (log at debug/info, never raise — one provider
    being down must not fail a scan).
  - Send `User-Agent: BookBrain (+https://github.com/bleedingrobot/BookBrain)`
    — their docs recommend it.
  - Per-instance in-memory cache keyed by isbn / `(title, author)` (like
    `OpenLibraryProvider._edition_series_cache`). Persistent caching is
    Phase 1.5 (see below).
- **`app/services/candidate_service.py`** — `default_candidate_service()`
  appends `HardcoverProvider(token=settings.hardcover_api_token)` **only when
  the token is set**. Zero behaviour change when unconfigured.

### Tests (`tests/test_hardcover_provider.py`, `respx`-mocked)

- ISBN query → candidate with series name + number from `book_series`.
- ISBN with no `book_series` → candidate, `series=None`, no crash.
- `search` path → candidates from `results.hits[].document`.
- GraphQL `{"errors": [...]}` response → `[]`.
- HTTP 429 with `Retry-After: 0` → one retry then `[]` (assert 2 requests).
- Empty token → provider short-circuits, makes no HTTP call.
- `test_candidate_service.py` — one case: `default_candidate_service()`
  includes Hardcover iff `hardcover_api_token` is set (monkeypatch settings).

### Notes / gotchas

- `identification_service.hash_evidence` folds candidate `source`/`title`/
  `authors`/`isbn13` into the `ai_decisions` cache key. Adding Hardcover
  candidates changes the hash **for newly-scanned books only** — already-
  processed files are skipped by `drive_file_id` and keep their cached
  decision. This is correct (new books should get the richer evidence);
  same reasoning as prompts/15 Stage D.
- **Offline corpus is flat by construction** — `pytest -m corpus` round-trips
  frozen recorded candidates, not live providers. A real measurement needs
  `eval_identification.py --live` (costs Anthropic credit → **ask James**).
  Ship Phase 1 without a measured delta, like Stages A/B/D.
- `pytest` + `pytest -m corpus` must stay green. Plain pytest never hits the
  network (respx / no token in test settings).
- GraphQL depth: `editions → book → book_series → series` is depth 4. Works
  today; if their depth-3 limit lands, split into two queries (edition→book_id,
  then book by id) — noted here so the fix is obvious.

### Acceptance

- Token unset → `default_candidate_service()` identical to today, no Hardcover
  calls anywhere.
- Token set → a scanned book with an ISBN Hardcover knows gets a
  `BookCandidate(source="hardcover")` row with a curated series, visible in
  the review UI and the re-identification audit.
- `cd backend && pytest` + `pytest -m corpus` green.

One commit.

---

## Phase 1.5 — persistent Hardcover cache (small, optional follow-up)

`reident_audit_service` and `description_service` each build a fresh
`default_candidate_service()` per run, so the in-memory cache only helps
within a single scan batch. A `hardcover_cache(isbn/query_key, response_json,
fetched_at)` table (or reuse the `book_candidates` rows already persisted by
`scan_service`) would cut repeat calls across runs and survive restarts.
Refresh entries older than ~30 days (Hardcover is continuously re-curated).
Do this only if the daily 5,000 limit actually starts biting.

---

## Phase 2 — real series membership in the library index

`library-viewer/src/lib/seriesGaps.ts` currently *guesses* missing series
entries from what's already in the library. Replace/augment with Hardcover's
actual canonical series list.

- Backend: `series_service.hardcover_series_books(series_id)` running the
  "Getting All Books in a Series" query with the dedup filters
  (`canonical_id: {_is_null: true}`, `is_partial_book: {_eq: false}`,
  `compilation: {_eq: false}`, `distinct_on: position`,
  `order_by: [{position: asc}, {book: {users_count: desc}}]`).
- Store the resolved list (position → title) on the series, surfaced in
  `bookbrain-index.json` per book's series.
- The viewer's "Missing books" filter then shows **named** missing entries
  ("you have 1, 2, 4 — missing #3 *Title*") instead of computed gaps, and
  `computeSeriesGaps` / `MAX_RUN_GAP` heuristics can be retired.
- Match a BookBrain series to a Hardcover series once (by name + author, or a
  stored `hardcover_series_id` on first match), then refresh on a schedule.

## Phase 3 — richer viewer metadata

Fold Hardcover fields into `bookbrain-index.json` for the viewer to display
and filter on:

- `genres`, `moods`, `tags`, `content_warnings`, `rating` / `ratings_count`,
  `pages`, `audio_seconds`, `book_category_id` (Novella / Graphic Novel /
  Light Novel / Collection…), `literary_type_id` (Fiction / Nonfiction).
- `cached_similar_book_ids` (~100 per book) → a "readers also liked" strip,
  cross-referenced against what's in the library.
- `description` as another source for the fill-missing-descriptions feature
  ([[project_bookbrain_descriptions]]).
- Characters → "books featuring X" (their `characters` search + `book_characters`).

All read-only catalogue data — no user data. Each field is independent, so
Phase 3 can land piecemeal.
