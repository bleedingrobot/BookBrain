# Task 30 — Reading status from Hardcover ("Read", ratings, "Want to read")

Run as its own fresh session. **Read first:** `prompts/25-hardcover-integration.md`
(constraints + posture — especially the **user-data licence line**),
`prompts/27-new-and-upcoming-releases.md` (the per-author pass + sidecar
pattern), `project_bookbrain_hardcover` + `project_bookbrain_metadata_sidecar`
+ `project_bookbrain_semantic_search` memories, `SPEC.md` § "library-viewer".

## The ask

The library-viewer knows nothing about what's been *read* — only a
per-browser reading *position* in the in-app reader. But James's Hardcover
account has it all: **420 books marked Read (353 rated), 227 want-to-read,
56 currently reading, 19 DNF** (722 `user_books` total, verified live
2026-09-09 with the token already in `.env`).

Pull that in and surface it: a "Read" badge/filter in the viewer, your
rating, and — the real payoff — the signals it unlocks (what to read next in
a series you own; which authors you actually read).

## Licence — read this before writing code

Hardcover's API terms forbid a **public/commercial product** from using
user-owned data (reviews/ratings/lists); BookBrain has only ever read
catalogue data. This task is the deliberate exception, and it's defensible
*only* because:

- it reads **James's own** `user_books` with **James's own** Personal Access
  Token, for **his own** self-hosted tool;
- nothing is redistributed — the sidecar lives in his private Drive folder;
- the viewer attributes it ("James has read this"), never presents it as
  anonymous/aggregate community data.

**Rules for this task:**
- ~~**Read only.**~~ Phase 3 (shipped 2026-09-09) adds status write-back —
  still James's own account/token/books, status only.
- The sidecar carries a `reader` name; the viewer labels the status with it.
- If James ever opens the viewer to a wider audience than his household,
  this gets revisited — leave a comment saying so where the sidecar is built.

## Hardcover shape (verified live 2026-09-09)

```graphql
query BookBrainReading($limit: Int!, $offset: Int!) {
  me {
    user_books(limit: $limit, offset: $offset, order_by: {id: asc}) {
      status_id            # 1 want-to-read · 2 currently-reading · 3 read · 5 DNF
      rating               # 0.5–5.0 or null
      last_read_date
      first_read_date
      read_count
      book {
        title
        contributions(limit: 1) { author { name } }
        editions(where: {isbn_13: {_is_null: false}}, limit: 1,
                 order_by: {users_count: desc}) { isbn_13 }
      }
    }
  }
}
```

`me { username }` = `bleedrobot`. Paginate `limit: 100` (~8 pages). Confirm
the `status_id` enum live (there may be a 4); map unknowns to `null`.

---

## Phase 1 — the sidecar + basic viewer display

**Stateless — no migration.** Mirror `fetch_global_anticipated`: fetch live,
match against the library, write a sidecar. Nothing persisted server-side.

### Backend — `app/services/hardcover_reading_service.py`

`fetch_reading(*, client=None) -> list[dict]`:
- page `me { user_books }`, map each to
  `{title, author, isbn13, status: 'read'|'reading'|'want'|'dnf'|None,
    rating: float|None, readDate: str|None, readCount: int}`;
- drop rows with no `status` and no `rating`.
- Any failure (no token, network, rate limit) → `[]` (best-effort, one
  non-critical call path).

### Sidecar — `bookbrain-reading.json`

`library_index_service.build_reading_payload(session, reading_rows)`:
- match each Hardcover row to an **organised** library file: ISBN-13 first
  (the library's `Identifier` isbn13/isbn10), then
  `normalize_title` + `normalize_person_name` (reuse `_norm_key`);
- `{version: 1, generatedAt, reader: "<display name>",
    books: {driveFileId: {status, rating, readDate, readCount}}}`;
- also a top-level `unmatched: {read, want, reading}` **count** (not the
  list) so the viewer can hint "James has read 118 books that aren't in the
  library".
- `regenerate_reading(creds, folder)` + a nightly step (token-gated, never
  fails the run) + `POST /api/library/reading/refresh` (fetch+match+write in
  one, since there's nothing to persist between them). Reader display name
  from `settings.hardcover_reader_name` (config, default the Hardcover
  `username`).

### Viewer

- `lib/reading.ts` — `fetchReading(token, folderId)`, the modifiedTime-gated
  localStorage-cache pattern from `recommendations.ts`. `ReadingEntry` per
  drive file id.
- `books.ts` — `BookRow` gains an optional `reading?: ReadingEntry`;
  `matchesFilter` gains `read` / `unread` / `want` / `reading` keys.
- `BookRow` — a small badge: `✓ Read` (+ `★ 4.5` if rated) / `Reading` /
  `Want to read` / `DNF`, muted, with a `title="{reader} — read Mar 2026"`.
- `App.tsx` — filter chips (only when the sidecar is non-empty):
  **Read**, **Unread**, **Want to read**. A one-line note under the search
  when `unmatched.read > 0`.
- `<Cover>` / row styling: optionally dim a read book slightly (like the
  wishlist's acquired items) — behind the "Read" being *shown*, not always.

### Tests
`test_hardcover_reading_service.py` (respx) — pagination, status mapping,
the no-status-no-rating drop, failure → `[]`.
`build_reading_payload` — ISBN match beats title match, unmatched counts,
organised-only. Viewer `reading.test.ts` — parse/cache; a filter-key check
in `books.test.ts`.

### Acceptance
Token unset → no sidecar, no chips, viewer unchanged. `cd backend && pytest`
+ `pytest -m corpus` green; viewer `npm test` + `npm run build` + lint green.
Live: `POST /api/library/reading/refresh` + `POST /api/library/index`, then
eyeball the Read chip + badges in the deployed viewer.

---

## Phase 2 — the payoffs (each independent, do after Phase 1 lands)

**All shipped 2026-09-09.** Next-up-in-series (`<ReadNext>`), author weighting
(`readingProfile`), favourites filter — commit `8cddae7`. **Want-to-read ↔
wishlist** — shipped 2026-09-09 (commit after Phase 3): `build_reading_payload`
now emits `wantUnowned[]` (want-to-read rows not in the library — read/reading
stay counts-only, licence); `reading.json` bumped to **v2**; the Wishlist
screen shows a "From your Hardcover want-to-read" card (one-tap Request →
existing `hitToItem` flow, hidden once listed/owned). 106 candidates live.


- **Next-up in a series** — combine `reading` + `seriesGaps.ts`: a home-screen
  "Read next" strip — series where you've read a contiguous run from #1 and
  **own** the next one you haven't read. And flag "you've read #1 and #3 but
  skipped #2". This is the feature `prompts/29`'s "next-up surfacing" roadmap
  note wanted; reading status is the missing input.
- **Reading profile → author weighting** — `readingProfile(reading)` = authors
  ranked by count of `read` books. Feed it into the `prompts/27` release
  strips (a new release from an author you've read 8 books of ranks above one
  you've read 1) and a "more from authors you've read" discovery row.
- ~~**Want-to-read ↔ wishlist**~~ — **DONE 2026-09-09.** Owned want-to-read
  books already surface via the existing `want` filter; unowned ones are
  wishlist candidates on the Wishlist screen (`wantUnowned[]` in the v2
  sidecar → "From your Hardcover want-to-read" card → Request).
- **Your favourites** — a filter/sort for your 4–5★ books; could seed the
  "readers also liked" pool weighting.

## Phase 3 — write-back — SHIPPED 2026-09-09 (James asked: "have it go both ways")

Two-way now. The viewer can't call Hardcover (static site, token is a backend
secret), so:

- **Viewer** writes a `bookbrain-reading-pending.json` queue to the Drive
  library folder. Sources: the expanded row's "Reading status" buttons
  (`onMarkRead` → `queueReadingChange`), and finishing an `.epub` in the
  reader (progress ≥ `FINISHED_FRACTION` on close → auto `markReadingStatus`
  `'read'`). The change is overlaid on the reading badges immediately with a
  `pending` flag ("syncing to Hardcover…"). Marking read also logs an
  `activity` event (`'read'`).
- **Backend** `hardcover_reading_service.apply_pending(changes)` resolves each
  book by ISBN (then title/author search), reads the existing `user_book`,
  and `insert_user_book` / `update_user_book` with `status_id` (+ today's
  `last_read_date` for `read`). Idempotent — a change already matching
  Hardcover counts as applied. `regenerate_reading` flushes the queue first,
  drops applied entries, then re-pulls and rewrites `bookbrain-reading.json`.
  Runs on `POST /api/library/reading` and nightly.

*Licence:* still the personal-automation case — James's own account, own
token, own books, only *status* written (never reviews or anyone else's
data). The module docstring carries the note.

Only status writes. Ratings/reviews stay read-only.

## Constraints / gotchas

- **Backend restart on Windows** — no `--reload`; the memory has the recipe.
- Hardcover paid tier 50k/day — one paged `me { user_books }` per night is
  nothing.
- `me { user_books }` returns **only James's** books — other household members'
  reading isn't available (they'd each need their own token). The viewer's
  attribution makes this honest; don't pretend it's per-viewer.
- One commit per phase. Update `prompts/25` + `prompts/README.md` +
  `ROADMAP.md` + the `project_bookbrain_hardcover` memory (+ a new
  `project_bookbrain_reading_status` memory) as each lands.
