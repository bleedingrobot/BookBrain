# Task 26 — Hardcover Phase 4: metadata badges, new & upcoming, descriptions, characters

Run as its own fresh session. **Read first:** `prompts/25-hardcover-integration.md`
(Phases 1–3, already shipped), the `project-bookbrain-hardcover` memory, and
`SPEC.md` § "Providers". This is the continuation of that work — four
semi-independent sub-features, all read-only Hardcover **catalogue** data.

## Shared context (how Phases 1–3 are built — copy the pattern)

- **Auth / limits / licence** — see prompts/25. `HARDCOVER_API_TOKEN` is set
  in `backend/.env` (live). Free tier 5,000/day, 60/min, burst 10. Only
  catalogue data (never user data) on the public viewer. **Never** use
  Hardcover image URLs (their DMCA clause) — the viewer already derives
  covers from ISBN via `covers.ts openLibraryCoverUrl`. Beta + unstable:
  every failure path returns nothing and the viewer degrades gracefully.
- **Backend shape** — `app/providers/metadata/hardcover.py` exports
  `hardcover_graphql(client, token, query, variables, bucket)` and
  `_TokenBucket` (instance-level, ~0.9 req/s, burst 8 — NOT a module
  singleton; dodges the conftest lock-rebind landmine). `hardcover_series_service`
  (Phase 2) and `hardcover_recs_service` (Phase 3) are the templates: a
  `refresh_*(session, *, limit=N, stale_after_days=M)` that selects
  organised-library rows with a null/stale `hardcover_synced_at`, capped,
  swallows every per-row error, commits once, returns a counts dict. Runs in
  `nightly.run_nightly` (token-gated, before `regenerate_library_index`,
  never fails the run) + a bounded `POST /api/library/...` route.
- **Migrations** — nullable `hardcover_json JSON` + `hardcover_synced_at
  TIMESTAMP` on `series` (Phase 2) and `books` (Phase 3), applied to James's
  real `epub_librarian.db` via `alembic upgrade head` (established pattern —
  2 nullable ADD COLUMNs, no batch rebuild). **`books.hardcover_json` already
  exists** — parts B/C extend it, no new migration.
- **Sidecars** — `bookbrain-index.json` (main, `INDEX_VERSION 3`,
  `build_index_payload`) for always-shown per-book data + the top-level
  `series` map. `bookbrain-recommendations.json` (separate, lazy,
  `build_recommendations_payload` / `regenerate_recommendations` /
  `_write_json_file`) for on-demand data. Viewer: `libraryIndex.ts` +
  `recommendations.ts`.
- **Windows restart gotcha** — after editing `models.py` / adding routes,
  `uvicorn --reload` leaves stale multiprocessing workers serving OLD code
  (routes 404 despite "Application startup complete"). Kill EVERY proc
  matching `uvicorn|app\.main|watchfiles|multiprocessing`, confirm
  `/api/health` is down, restart, poll `/openapi.json` until the new route
  appears. (In the `project_bookbrain_orphaned_dev_servers` memory.)

**Suggested order: B → A → C → D.** B and C piggyback on the existing recs
query (nearly free); A extends Phase 2; D is optional.

---

## Part B — metadata badges & filters (lead with this)

Per-book Hardcover metadata, shown as badges in the library and usable as
filters. **No new Hardcover calls** — fold the fields into the query
`hardcover_recs_service` already runs.

### Live-validated fields (on the `books` type)

`rating` (numeric, Hardcover avg), `ratings_count`, `pages`,
`book_category_id` (1 Book / 2 Novella / 3 Short Story / 4 Graphic Novel /
5 Fan Fiction / 6 Research Paper / 7 Poetry / 8 Collection / 9 Web Novel /
10 Light Novel), `literary_type_id` (1 Fiction / 2 Nonfiction),
`cached_tags` (jsonb — has genres/moods), plus `audio_seconds`. Genres/moods
are cleanest off the `search` Book document (`genres`, `moods` — top 5 each)
if `cached_tags` is awkward to parse; check both live.

### Backend

- `hardcover_recs_service`: add the meta fields to the `_BOOK_BY_ISBN` query's
  `book { ... }` selection; store `books.hardcover_json.meta = {rating,
  ratingsCount, pages, category, literaryType, genres: [...], moods: [...]}`
  alongside `similar`. Map the id enums to strings server-side.
- `library_index_service.build_index_payload`: add `meta` to each per-book
  entry (only the fields that are set). `INDEX_VERSION` → 4.
- No new route needed — `book-recs/refresh` already repopulates
  `hardcover_json`; a full re-run picks up meta for books done before this
  change (their `hardcover_synced_at` is set, so bump the query to also
  refresh rows whose `hardcover_json` has no `meta` key, OR just let the
  45-day staleness carry it — note which in the commit).

### Viewer

- `libraryIndex.ts`: parse `meta` into `IndexEntry`.
- `BookRow.tsx`: a rating pill (★ 4.2), a category badge (Novella / Graphic
  Novel / Light Novel — skip plain "Book"), page count in the expanded
  section, genre chips.
- `App.tsx` + `books.ts`: a **genre facet** — chips like the existing
  author/series filters (`matchesFilter` gains a `genre:<g>` key), populated
  from the genres present in `allRows`. Maybe a "Fiction / Nonfiction" toggle.
- `books.ts` `SORTS`: an optional "Rating" sort.

### Tests

Backend: recs service stores `meta`; index payload carries it. Viewer:
`libraryIndex.test.ts` parse; `books.test.ts` genre filter.

---

## Part A — "New & upcoming" in a series

Phase 2 already stores each series' canonical book list. Add release dates
and surface what's **coming** or **recently out** that you don't own.

### Backend

- `hardcover_series_service`: the `_SERIES_BOOKS` query's `book { title }` →
  `book { title release_date }`. Store `releaseDate` per entry in
  `series.hardcover_json.books`. (One-line change; the nightly staleness
  refresh carries it, or force a full re-run.)
- Nothing else server-side — the split is a viewer computation.

### Viewer

- `seriesGaps.ts` / a sibling: given owned positions + the catalogue with
  dates, compute:
  - **missing** — unowned integer positions *below* max-owned (Phase 2, keep).
  - **next up** — the lowest unowned position *above* max-owned with a
    `release_date <= today`.
  - **upcoming** — unowned positions with a future `release_date`.
- `BookRow.tsx` expanded section: below "Missing from …", add
  "Next in {series}: #N *Title*" and "Coming: #N *Title* — <month year>".
- **Gotcha (seen live):** Hardcover has future *placeholder* rows —
  "Untitled Stormlight Archive #10", `release_date` "2035-01-01". Filter out
  titles matching `/^untitled\b/i` and treat a date > ~3 years out as "no
  real date". Novella positions (0.5, 3.5) already handled by "integer only".
- Optional: a **"Coming soon"** filter chip / small screen aggregating
  `upcoming` across every series in the library.

### Tests

`seriesGaps.test.ts`: next-up vs upcoming vs missing, placeholder filtering,
far-future-date handling.

---

## Part C — Hardcover as a description source (small)

`description_service` (fill-missing-descriptions) uses Google Books + Open
Library. Hardcover has `book.description` and it's already fetched.

- `hardcover_recs_service`: store `books.hardcover_json.meta.description`
  (plain text, cap ~1500 like `library_index_service._plain_text`).
- `description_service`: check `book.hardcover_json.meta.description` **first**
  (zero API cost, zero credits) before the provider lookups. Keep the same
  "only fill when empty, never overwrite a human/AI one" rule.
- Test: a book with a Hardcover description and no others gets filled from it.

---

## Part D — characters (optional, lowest priority)

`search(query_type: "Character")` returns `name`, `books` (titles the
character appears in, spoiler-safe), `author_names`, `slug`. `book_characters`
on the `books` type lists a book's characters.

- Idea: on a book's expanded row, "Characters: Vin, Kelsier, …" (from
  `book_characters`, stored in `books.hardcover_json.meta.characters`), each
  a link to `hardcover.app/characters/{slug}`; or a search box "books
  featuring X" that hits the character search and cross-references the
  library.
- Only build if A–C landed and it still seems worth it. Scope it yourself.

---

## Acceptance (per part)

- Token unset → no new fields, viewer unchanged.
- Backend `cd backend && pytest` + `pytest -m corpus` green.
- Viewer `npm test` + `npm run build` + `npm run lint` green.
- Live-validate each part against the real token before committing (there's
  quota — check the `RateLimit` response headers).
- One commit per part. Update `prompts/25` + `prompts/README.md` +
  `ROADMAP.md` + the `project-bookbrain-hardcover` memory as each lands.
- After a backend part: restart cleanly (see the gotcha above), run a small
  `book-recs/refresh` / `series-catalog/refresh` to backfill the new field,
  then `POST /api/library/index` (works headless — the backend has the
  stored Google refresh token). The nightly job converges the rest.
