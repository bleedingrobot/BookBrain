# Task 27 — "New & Upcoming" / "Recent Releases" discovery strips

Run as its own fresh session. **Read first:** `prompts/25-hardcover-integration.md`
(constraints + posture), `prompts/26-hardcover-phase-4.md` (Part A already
ships per-series `releaseDate`), the `project-bookbrain-hardcover` memory, and
`SPEC.md` § "Providers".

## The ask

James saw Hardcover's dashboard "New & Upcoming" page (two horizontal
cover carousels: *Upcoming Releases* = future books, most-anticipated first;
*Recent Releases* = last ~3 months). He wants the same in the library-viewer,
**styled like the existing scrolling cover strip** (`RecentMarquee` /
"Recently added") — covers gliding across, click a cover to see the book and
one-tap **Request** it into the wishlist.

Not a global Goodreads feed — **filtered to what James's library already
knows** (authors he reads, series he owns). A romantasy release is noise if
he only reads epic fantasy. A global "most anticipated" strip is an optional
extra (Part 3), behind a setting, off by default.

## Reuse what's already there

- **`RecentMarquee.tsx`** — the exact visual: `marquee` / `marquee-track` CSS,
  duplicated `<Track>` for the seamless loop, edge fade gradients, a
  "Full screen ⤢" TV mode, `SECONDS_PER_COVER` speed. Generalise it (props for
  title + items + an `onPick`) rather than copy-paste three times.
- **`useMarqueeCovers.ts`** — eager, *verified* cover resolution (local Drive
  thumbnail → Open Library by ISBN, only hands back a URL after an `Image()`
  probe confirms it loads). These books have no local cover, so it's the
  OL-by-ISBN path only — a lighter variant keyed by isbn13 is fine.
- **`covers.ts openLibraryCoverUrl`** — the only cover source. **Never a
  Hardcover image URL** (their DMCA clause — see prompts/25).
- **`wishlist.ts addToWishlist(token, folderId, item, by, ownedRows)`** →
  `'added' | 'already-listed' | 'owned'`, and the `request` activity event —
  the Request button from "Readers also liked" (`BookRow.tsx` `ReadersAlsoLiked`)
  is the pattern to copy for the click-through card.
- **`seriesGaps.ts`** — `computeSeriesGaps(rows, index.series)` **already
  computes `nextUp` + `upcoming` per series** (Part A). Part 1 below is mostly
  just flattening that.

---

## Part 1 — "In your series" strips (cheap: no new Hardcover calls)

Part A already stores every matched series' book list with `releaseDate`, and
`seriesGaps.ts` already splits the entries above what you own into `nextUp`
(released, unowned) and `upcoming` (real future date). Aggregate across the
whole library:

- **`seriesGaps.ts`** — a helper `collectSeriesReleases(gaps)` →
  `{ recent: SeriesReleaseEntry[], upcoming: SeriesReleaseEntry[] }`, each
  entry carrying its series name + `hardcoverSlug` (add those to
  `SeriesReleaseEntry`). "recent" = `nextUp` plus any `upcoming` whose date has
  since passed; "upcoming" = `upcoming`, soonest first. Dedupe, cap ~30 each.
- **`App.tsx`** — two `<ReleaseMarquee>` strips near the existing one, only
  when non-empty:
  - **"New in your series"** — `recent`
  - **"Coming soon in your series"** — `upcoming`, each captioned with its
    month/year (reuse `BookRow.tsx`'s `monthYear`).
- Click a cover → a small card (title, author, series #N, the blurb if we have
  one, a Hardcover series link) + **Request** → wishlist. A
  `<ReleaseCard>` shared with Part 2.
- Covers: OL-by-ISBN — but a `SeriesCatalogBook` has no ISBN today. Two
  options, pick one and note it in the commit:
  1. add `isbn13` to the per-entry data in `hardcover_series_service._fetch_books`
     (`book { title release_date editions(where:{isbn_13:{_is_null:false}}
     limit:1 order_by:{users_count:desc}){ isbn_13 } }`) — one extra field, no
     extra call; bump `INDEX_VERSION` → 5;
  2. resolve covers viewer-side by title+author via the existing wishlist
     Google-Books search (`bookSearch.ts`) — no backend change, more requests.
  (1) is cleaner. Do (1).

### Tests
`seriesGaps.test.ts` — `collectSeriesReleases`: recent vs upcoming, the
"future date that has passed" reclassification, dedupe, cap. Backend: the new
`isbn13` field lands in `hardcover_json.books` + the index `series` map.

---

## Part 2 — "From authors you read" (backend, rate-limit-aware)

Series only covers series. Most of the library is standalones / authors. Add a
per-author Hardcover pass.

### Backend — `app/services/hardcover_new_releases_service.py`

`refresh_new_releases(session, *, limit=120, stale_after_days=14)`:

1. Select **authors** with ≥1 organised book, whose `Author.hardcover_synced_at`
   (new nullable column, same migration pattern as prompts/25 Phase 2/3 — 2
   nullable ADD COLUMNs on `authors`: `hardcover_json JSON`,
   `hardcover_synced_at TIMESTAMP`) is null/stale, capped at `limit`.
2. Per author, **one** query — resolve the author id and their recent/near
   books in a window:
   ```graphql
   query ($name: String!, $since: date!) {
     authors(where: {name: {_eq: $name}}, limit: 1) {
       id
       contributions(
         where: {book: {release_date: {_gte: $since}}}
         order_by: {book: {users_count: desc}}
         limit: 12
       ) {
         book {
           title release_date users_count
           book_category_id
           image  # DO NOT store/serve — for a sanity check only, never persisted
           editions(where: {isbn_13: {_is_null: false}} limit: 1
                    order_by: {users_count: desc}) { isbn_13 }
           cached_tags
         }
       }
     }
   }
   ```
   `$since` = today − 4 months (covers "recent" + everything future).
   **Validate this shape live first** — `authors`/`contributions` filtering
   may need `people`/`author_id` instead; check against the real token.
3. Store `Author.hardcover_json = {id, books: [{title, releaseDate, isbn13,
   category, genres}]}`, drop `/^untitled\b/i` + dates >3y out (Part A's
   `realReleaseDate` rule — factor it into a shared util if easy).
4. **Rate-limit-aware** — wrap the loop in `try/except HardcoverRateLimited:
   break` and per-row `except HardcoverUnavailable: continue`, commit **per
   author** (all three patterns already in `hardcover_series_service` /
   `hardcover_recs_service` after commit `250ac8b` — copy them). Return a
   counts dict with `rate_limited` on early stop.

~1 call/author. A few hundred authors → converges over several nights at
`limit=120` (5,000/day cap; the series + recs refreshes share it).

### Sidecar + wiring

- **`bookbrain-new-releases.json`** — a **separate** sidecar (like
  `bookbrain-recommendations.json`): `{version, generatedAt, recent: [...],
  upcoming: [...]}` where each item is `{title, author, isbn13, releaseDate,
  genres, hardcoverSlug?}`, built from `Author.hardcover_json` **minus books
  already in the library** (match on normalized title+author) **and** already
  on the wishlist. `library_index_service.build_new_releases_payload` +
  `regenerate_new_releases(creds, folder)` + `_write_json_file`.
- **`nightly.run_nightly`** — one more step after the recs step, token-gated,
  never fails the run; write the sidecar after.
- **Routes** — `POST /api/library/new-releases/refresh?limit=&stale_days=`
  (fetch/resolve) + `POST /api/library/new-releases` (write the file). Bounded,
  mirror the Phase 3 routes.
- Merge Part 1's series entries into the same sidecar so the viewer has one
  feed (tag each `source: 'series' | 'author'`).

### Viewer

- **`lib/newReleases.ts`** — `fetchNewReleases(token, folderId)`, the
  modifiedTime-gated localStorage-cached pattern from `recommendations.ts`.
  **Lazy** — App fetches it once, on mount or first idle, not blocking first
  paint.
- **`App.tsx`** — the two `<ReleaseMarquee>` strips consume the merged feed;
  a "See all" opens a full `<NewReleasesScreen>` (grid, filter by
  recent/upcoming + genre, the same Request buttons) — model it on
  `WishlistScreen.tsx`.

### Tests
`test_hardcover_new_releases_service.py` (respx) — author match, window
filter, placeholder drop, rate-limit stop-early doesn't wipe, stale
selection, `limit`. `build_new_releases_payload` — excludes owned + wishlisted.
Viewer `newReleases.test.ts` — parse/cache; a `ReleaseMarquee` render check.

---

## Part 3 — global "Most anticipated" (optional, behind a setting)

One `books(where: {release_date: {_gte: today}}, order_by: {users_count: desc},
limit: 40)` query in the nightly, stored in the same sidecar under
`global: [...]`. Viewer shows a third strip **only if** a
`showGlobalReleases` toggle in `SettingsForm` is on (default off). Check live
whether Hardcover exposes an official "Most Anticipated" `list` worth mirroring
instead of a raw `users_count` sort.

---

## Constraints / gotchas

- **Hardcover daily cap is 50,000** (James upgraded to the paid "Supporter"
  tier 2026-09-08 — per-minute still 60, burst 30). Comfortable headroom for a
  fourth service, but the `HardcoverRateLimited` break (commit `250ac8b`) is
  still the safety net if a runaway loop ever burns it.
- **Never persist or serve Hardcover `image` URLs** — covers are OL-by-ISBN
  only, and a release with no resolvable cover is simply dropped from the
  strip (`RecentMarquee` already filters to books with a verified cover).
- **Backend restart on Windows** — no `--reload` (it leaves stale workers);
  `uvicorn app.main:app --port 8000`, then poll `/openapi.json` for the new
  routes. (`project_bookbrain_orphaned_dev_servers`.)
- Migration: 2 nullable ADD COLUMNs on `authors`, applied with `alembic
  upgrade head` — the established pattern, no batch rebuild.

## Acceptance (per part)

- Token unset → no sidecar, no strips, viewer unchanged.
- Part 1 alone is worth shipping: "Coming soon in your series" from data
  that's already in the index.
- `cd backend && pytest` + `pytest -m corpus` green; viewer `npm test` +
  `npm run build` + `npm run lint` green.
- Live-validate the `authors`/`contributions`/`books` query shapes against the
  real token before wiring (there's a 5,000/day budget — a dozen probe calls
  is fine).
- One commit per part. Update `prompts/25` + `prompts/README.md` + `ROADMAP.md`
  + the `project-bookbrain-hardcover` memory as each lands.
- After a backend part: restart clean, run a small
  `new-releases/refresh` + `POST /api/library/new-releases` +
  `POST /api/library/index`, or let the nightly converge it.
