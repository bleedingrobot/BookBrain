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
- **Rate limits:** Free tier 5,000/day, 60/min, burst 10. **James upgraded to
  the paid "Supporter" tier 2026-09-08 → 50,000/day**, 60/min, burst 30. One
  request may contain ≤5 top-level queries (or 1 `search`). A `search` counts
  as 1. Response carries `RateLimit` / `x-ratelimit-daily-*` / `Retry-After`
  headers; `hardcover_graphql` raises `HardcoverRateLimited` on a daily 429 so
  a bulk run stops clean (commit `250ac8b`).
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

## Phase 2 — real series membership in the library index — DONE 2026-09-08

`library-viewer/src/lib/seriesGaps.ts` *guesses* missing series entries from
what's in the library. Phase 2 adds Hardcover's canonical list on top, as an
**"According to Hardcover…"** informational layer — the heuristic stays as
the fallback.

### What the live API actually gives (validated with the real token)

- `search(query_type: "Series")` → `document` with `id` (a **string** in
  search results, int elsewhere), `name`, `author_name`, `books` (top-5
  titles), `books_count`, `primary_books_count`, `slug`.
- The "books in a series" query works, but the data is **good, not clean**:
  positions like `0.5` / `3.5` / `null` (novellas, companion), occasional
  foreign-language titles in a slot, and **overlapping series** ("The
  Mistborn Saga" id 5452 = 10 primary books vs "The Mistborn Saga: The
  Original Trilogy" id 10001 = 3). `order_by: [{position: asc}, {book:
  {users_count: desc}}]` + `distinct_on: position` is essential — without the
  `users_count` tiebreak you get a random (often foreign) edition's title.

So the feature is deliberately conservative: match best-effort, show it
**labelled as Hardcover's** with a link to the Hardcover series page so
James can eyeball a bad match, only surface **integer** positions, and keep
`computeSeriesGaps` as the fallback when there's no match.

### Backend

- **Migration** — 2 nullable columns on `series`:
  `hardcover_synced_at TIMESTAMP` (drives the "needs refresh?" query) and
  `hardcover_json JSON` (`{id, name, slug, primaryCount, books: [{position,
  title}], match: "auto"|"manual"|"none"}`). One JSON blob keeps the column
  count down; `match: "none"` records "we looked, found nothing" so the
  nightly job doesn't re-search it every night; `match: "manual"` (James
  sets `hardcover_json.id` by hand) is never re-matched, only its book list
  is refreshed.
- **`app/providers/metadata/hardcover.py`** — extract the POST/429/retry/
  error boilerplate into a module fn `hardcover_graphql(client, token, query,
  variables, bucket)`; `HardcoverProvider` uses it. Export `ENDPOINT`,
  `_USER_AGENT`, `_TokenBucket`.
- **`app/services/hardcover_series_service.py`** —
  `refresh_series_catalog(session, *, limit=300, stale_after_days=30)`:
  1. pick `Series` rows that (a) have a book on an `organised` file and
     (b) `hardcover_synced_at` is null or older than `stale_after_days`,
     capped at `limit` (converges over a few nights, stays well under the
     5,000/day Hardcover cap).
  2. per series: if `match == "manual"` keep the id; else
     `search(query_type:"Series")` → best hit by
     `_series_match_key`-overlap + author agreement → id, or record
     `match:"none"`.
  3. fetch the book list (the dedup-filtered query), keep entries with a
     numeric position, store `hardcover_json`, stamp `hardcover_synced_at`.
  Its own `_TokenBucket`. Returns `{matched, refreshed, unmatched, skipped}`.
- **`nightly.run_nightly`** — one new step, right before
  `regenerate_library_index`, only when `settings.hardcover_api_token` is set.
- **`library_index_service.build_index_payload`** — add a top-level
  `series` map: `{ "<Series.name>": {hardcoverId, hardcoverName,
  hardcoverSlug, books: [{position, title}]} }` for every series with a
  match. `INDEX_VERSION` → 3.
- **`POST /api/library/series-catalog/refresh`** — manual trigger (bounded,
  same as `/index`), so James can run it against the live backend without
  waiting for nightly.

### Viewer

- **`libraryIndex.ts`** — parse the new `series` map into
  `LibraryIndex.series: Record<string, SeriesCatalog>`.
- **`seriesGaps.ts`** — `computeSeriesGaps(rows, catalog?)`: when a catalog
  entry exists for a series, `missing` = its integer positions not owned
  (capped at `max(ownedMax, lastCatalogInt)`), each **with a title**;
  otherwise the current heuristic unchanged. `SeriesGap` gains
  `missingTitles?: Record<number, string>` and `source: 'hardcover' | 'guess'`.
- **`BookRow.tsx`** — "Missing from {series}: #3 *The Hero of Ages*, #4 …"
  when titled; a small "via Hardcover" caption linking
  `https://hardcover.app/series/{slug}`.
- Keep `MAX_RUN_GAP` and all its tests — still the no-match path.

### Tests

- `test_hardcover_series_service.py` (respx): series search → match, no-match
  → `match:"none"`, manual → not re-matched, non-integer positions dropped,
  stale-only selection, `limit` bound.
- `library_index_service` test: `series` map shape.
- viewer `seriesGaps.test.ts`: catalog path (titled missing), fallback path
  unchanged, cap logic.

### Acceptance

- Token unset → nothing changes (no `series` map, heuristic as today).
- Token set, after a refresh → a matched series shows Hardcover's named
  missing entries + a link; an unmatched series falls back to the guess.
- `cd backend && pytest` + `pytest -m corpus` green; viewer `npm test` +
  build + lint green.

## Phase 3 — "Readers also liked" (recommendations → wishlist) — DONE 2026-09-08

Every Hardcover `books` row carries `cached_similar_book_ids` — ~100 book ids,
pre-ranked most→least similar (a precomputed similarity graph, plain
catalogue data). Validated live: *Mistborn: The Final Empire* → The Way of
Kings, A Game of Thrones, The Name of the Wind, The Eye of the World, Red
Rising. This is Hardcover's recommendation engine.

The feature: on a book's expanded row in the viewer, a **"Readers also
liked"** list — each entry shows title + author + (OL) cover, and is either
tagged *In library* / *On wishlist* or carries a one-tap **Request** button
that drops it straight into the wishlist ([[project_bookbrain_wishlist]]) as
`requestedBy: <viewer name>`. Recommendations that feed the acquire flow you
already built.

**Licence:** the similar-books graph is catalogue data → fine on the public
viewer. Do **not** use Hardcover's cover image URLs (their DMCA clause) — the
viewer already derives a cover from ISBN via Open Library (`covers.ts
openLibraryCoverUrl`). A *personalised* Hardcover recommendation feed (built
from James's own Hardcover reading) is user data → **out of scope** for the
shared viewer.

### Backend

- **Migration** — mirror Phase 2 but on `books`: `hardcover_json JSON`
  (`{id, similar: [{title, author, isbn13}]}` — top ~15, resolved) +
  `hardcover_synced_at TIMESTAMP`.
- **`app/services/hardcover_recs_service.py`** —
  `refresh_book_recs(session, *, limit=400, stale_after_days=45)`:
  1. pick `Book` rows with an ISBN + an `organised` file + null/stale
     `hardcover_synced_at`, capped.
  2. per book: `editions(where isbn){ book { id cached_similar_book_ids } }`
     — one call, gets the Hardcover id + the similar-id list.
  3. resolve the top ~20 ids: `books(where: {id: {_in: [...]}}) { id title
     contributions(limit: 1){ author { name } } editions(where: {isbn_13:
     {_is_null: false}}, limit: 1){ isbn_13 } }` — one call. Keep the first
     ~15 that resolve to a real title, drop any already == this book.
  4. store `{id, similar: [...]}`, stamp `hardcover_synced_at`.
  ~2 Hardcover calls/book. Own `_TokenBucket`. Full library ≈ 4,500 calls →
  converges over ~2 nights at `limit=400`, or loop the manual route.
  Returns `{resolved, empty, failed}`.
- **`library_index_service`** — a **separate** sidecar
  `bookbrain-recommendations.json` (`INDEX v1`: `{version, generatedAt,
  books: { "<driveFileId>": [{title, author, isbn13}] }}`), written by a new
  `regenerate_recommendations(creds, folder)` — kept out of the main index so
  the ~1MB index stays lean and the viewer fetches recs only on demand.
- **`nightly.run_nightly`** — recs refresh step + the recs-file write, after
  the series step, token-gated, never fails the run.
- **Routes** — `POST /api/library/book-recs/refresh?limit=` (fetch/resolve),
  `POST /api/library/recommendations` (write the file). Both bounded.

### Viewer

- **`lib/recommendations.ts`** — `RecBook {title, author, isbn13}`;
  `fetchRecommendations(token, folderId)` → `Record<driveFileId, RecBook[]>`,
  the same modifiedTime-gated + localStorage-cached pattern as
  `libraryIndex.ts`. **Lazy**: App only calls it the first time a row is
  expanded.
- **`lib/wishlist.ts`** — new `addToWishlist(token, folderId, item, by)`
  helper: load → dedup (`alreadyListed`) → append `hitToItem` → save. So a
  Request from anywhere is one call.
- **`BookRow.tsx`** expanded section — "Readers also liked" block:
  per rec, title — author, an OL `Cover` by ISBN, and `In library`
  (`libraryMatch` from `wishlist.ts` vs `allRows`) / `On wishlist` / a
  **Request** button → `onRequestBook(rec)`.
- **`App.tsx`** — lazy `recommendations` state; `onExpand` triggers the
  fetch; pass `recs[row.file.id]` down; `onRequestBook` calls `addToWishlist`
  and toasts.

### Tests

- `test_hardcover_recs_service.py` (respx): id + similar-ids in one call,
  batch-resolve, self-reference dropped, no-ISBN book skipped, empty
  `cached_similar_book_ids` → stored `similar: []`, `limit`/stale selection.
- `library_index_service` test: recs payload shape.
- viewer: `recommendations.test.ts` (parse/cache), `wishlist.test.ts`
  (`addToWishlist` dedup), a `BookRow` render check if feasible.

### Acceptance

- Token unset → no recs file, no "Readers also liked" block.
- After a refresh → expanding a book shows its similar books; Request adds
  one to the wishlist with the requester stamped; already-owned ones are
  tagged, not requestable.
- `cd backend && pytest` + `pytest -m corpus` green; viewer `npm test` +
  build + lint green.

## Phase 4 — "New & upcoming" + richer metadata

**Split out into its own work-prompt: [`26-hardcover-phase-4.md`](26-hardcover-phase-4.md)**
(parts B metadata badges → A new & upcoming → C descriptions → D characters).
Original scoping notes kept below. **Parts B + A + C shipped 2026-09-08** —
B: curated rating/pages/category/genres → `hardcover_json.meta` →
`bookbrain-index.json` v4 → viewer badges, a genre facet, and a rating sort.
A: series `release_date` per entry → viewer "Next in …" / "Coming …" +
a "Coming soon" filter chip. C: `meta.description` tried first (free) by
fill-missing-descriptions. Part D (characters) not started.

- **New & upcoming** — for every author/series in the library, Hardcover
  `books` with `release_date` in the last ~18 months **or** future, that
  aren't already owned → a screen + the same Request button. Gotchas seen
  live: future *placeholder* rows ("Untitled Stormlight Archive #10", dated
  2035) need a name filter + `release_date <= today` for "recent" vs a
  separate "upcoming" bucket; novella positions (5.5, 5.6) as with Phase 2.
- **Metadata badges/filters** — `genres`, `moods`, `tags`,
  `content_warnings`, `rating`/`ratings_count`, `pages`, `audio_seconds`,
  `book_category_id` (Novella / Graphic Novel / Light Novel / Collection),
  `literary_type_id` (Fiction / Nonfiction) → into the main index, viewer
  shows badges + facet filters.
- **`description`** as another source for fill-missing-descriptions
  ([[project_bookbrain_descriptions]]).
- **Characters** → "books featuring X" (`characters` search + `book_characters`).

All read-only catalogue data. Each item is independent and can land
piecemeal.
