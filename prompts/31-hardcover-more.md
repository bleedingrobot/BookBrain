# Task 31 — More from Hardcover: tags, reading life, discovery

Run as its own fresh session. **Read first:** `prompts/25-hardcover-integration.md`
(licence + posture — the bit that matters most here), `prompts/26-hardcover-phase-4.md`
(Part B built `hardcover_json.meta`), `prompts/27-new-and-upcoming-releases.md`
(release strips + `<Marquee>`), `prompts/30-reading-status.md` (the reading
sidecar + the "own data" exception), and the memories
`project-bookbrain-hardcover`, `project-bookbrain-reading-status`,
`project-bookbrain-metadata-sidecar`, `project-bookbrain-semantic-search`.

## The ask

James: *"what other cool stuff could we leverage from Hardcover?"* → *"all of
them please."* Ten ideas, grouped into nine parts. **Each part is independently
shippable — one commit per part, in the order below (value ÷ effort).** Stop
and check in with James between parts if a part turns out bigger than its
estimate.

## Licence — which parts touch user data

`prompts/25`: the **shared** library-viewer may only use Hardcover *catalogue*
data. `prompts/30` opened one deliberate exception — **James's own** data via
**his own** token for **his own** tool, shown *attributed* to a named reader,
never redistributed. Sort each part into the right bucket:

| Part | Data | Bucket |
|---|---|---|
| A tags / moods / pace / content warnings | catalogue | shared — fine |
| B rating-aware recs & want triage | James's ratings (already synced) | own-data (already in the reading sidecar) |
| C reading goal + stats | James's `user_books` + goal | own-data — attribute to the reader |
| D followed authors | James's follow list | own-data — used only to rank his own library's strips |
| E community lists | catalogue (public lists) | shared — fine |
| F Hardcover Prompts | catalogue (public Q&A) | shared — fine |
| G trending | catalogue | shared — fine |
| H edition metadata | catalogue | shared — fine |
| I reading-progress sync | James's reading progress | own-data — biggest one, do last |

For the own-data parts: same guardrail comment as `hardcover_reading_service.py`,
and if the viewer ever goes wider than the household, revisit.

## Hardcover schema — verify every query live first

Hardcover's GraphQL has surprised us before (the `statuses` enum table isn't
readable by a token — `prompts/30`; `authors → contributions` shape — `prompts/27`).
**Before building each part, run the query in isolation** (a scratch script
hitting `ENDPOINT` with the `.env` token) and confirm the shape. The field
names below are best-guess from the public site, not verified.

---

## Part A — moods & content warnings — SHIPPED 2026-09-10 (commit pending)

**Extends `prompts/26` Part B.** `cached_tags` (already fetched in
`hardcover_recs_service._book_meta`) is a bucketed blob. Live check
2026-09-10: the buckets are `Genre`, `Mood`, `Content Warning`, `Tag`,
`Group` — **there is no `Pace` bucket and `book.pace` is null**, so pace was
dropped from this part (Hardcover's slow/med/fast is derived from reader
speed data, not exposed cleanly). Shipped: pull `Content Warning`, surface
the already-stored `Mood`.

- `_book_meta` → `meta["contentWarnings"]` (top 8 `Content Warning` tags);
  `_tag_names` gained a `limit` arg.
- `library_index_service._META_KEYS` += `"contentWarnings"`; **INDEX_VERSION
  6**.
- Viewer: `IndexMeta.contentWarnings`; `books.ts` `topMoods` + `mood:<n>`
  filter key; `App.tsx` `moodFacets` chips after the genre chips +
  `filterToMood`; `BookRow.tsx` clickable mood badges (violet) + a
  `<details>` "Content warnings (N)" disclosure in the expanded row.
- Backend 672 + corpus green; viewer 163 + build + lint green. Needs a
  book-recs refresh + index regen to populate `contentWarnings` live (nightly
  will converge; force with `POST /api/library/book-recs/refresh?stale_days=0`
  then `POST /api/library/index`).

---

### Original notes

`cached_tags`: `Genre` / `Mood` / `Content Warning` / `Tag` buckets. Today we
extract `Genre` and `Mood`; `Mood` is stored but never shown; `Content
Warning` wasn't pulled.

### Backend
- `hardcover_recs_service._book_meta` — add:
  - `meta["pace"]` — single string, the top `Pace` tag (`"Fast"` / `"Medium"` /
    `"Slow"`), if present. (Confirm the bucket key — might be `"Pace"` or live
    under `Tag`.)
  - `meta["contentWarnings"]` — top ~6 `Content Warning` tag names. Store the
    raw names; don't editorialise.
- `library_index_service` — add `"pace"`, `"moods"`, `"contentWarnings"` to
  `_META_KEYS`. Bump `INDEX_VERSION` → 6.
- No new Hardcover call — this rides the existing `refresh_book_recs` pass.
  Note in the commit that the values only appear for books whose recs row has
  been refreshed since (nightly backfills the rest).

### Viewer
- `libraryIndex.ts` `IndexMeta` — `pace?: string`, `contentWarnings?: string[]`
  (`moods` already typed? add if not). Validate like the existing arrays.
- `books.ts` — `matchesFilter` gains `mood:<name>` and `pace:<name>` keys
  (mirror `genre:`). A `topMoods(rows)` helper mirroring `topGenres`.
- `App.tsx` `filterChips` — a **Mood** chip group and a **Pace** chip group
  under the existing genre facet, populated from `topMoods` / distinct paces,
  shown only when non-empty.
- `BookRow.tsx` expanded section — a **"Content warnings"** disclosure:
  collapsed by default (`<details>`), lists `meta.contentWarnings`. Muted
  styling — informational, not alarmist. If empty, no row.
- Optional: a tiny pace pill (`⚡ Fast` / `🐌 Slow`) next to the rating pill.

**Done when:** index v6; mood + pace chips filter; content-warning disclosure
shows on books that have them; viewer tests for the new `matchesFilter` keys +
`topMoods`; `npm test` + build + lint green; backend `_book_meta` test covers
pace + content-warning extraction.

---

## Part B — rating-aware recommendations & want-to-read triage — SHIPPED 2026-09-10

The reading sidecar (`bookbrain-reading.json`, `prompts/30`) already carries
`books[driveId].rating`. Uses it, viewer-only, no backend change / no backfill.

### B1 — weight "readers also liked" by your taste — SHIPPED
- **`RecBook` has no genres** (the recs sidecar is `{title, author, isbn13}`
  only), so the genre-affinity idea would need a backend change + another
  backfill. Shipped the **author-affinity** version instead:
  `reading.ts` `authorAffinity(reading, rowsById)` → `Map<author, meanRating>`
  (an unrated DNF counts as 2). `App.tsx` `authorAffinityMap` memo →
  `<BookList authorAffinity>` → `<BookRow recAffinity>` → `ReadersAlsoLiked`
  stable-sorts recs by `affinity.get(rec.author) ?? 3.5` desc, Hardcover order
  as the tiebreak. Gentle re-rank, no hard filter.
- Follow-up if ever worth it: add `genres` to the recs sidecar
  (`hardcover_recs_service` already resolves each rec book — one `cached_tags`
  field) for genre-affinity, which beats author-affinity for discovery.

### B2 — want-to-read triage — SHIPPED
- `books.ts` `quickWinScore(row)` = length tier (`pages<350` → +1, `≤600` → 0,
  `>600` → −1; unknown 0) + `(rating − 3.5)` where rating is your own then the
  community `meta.rating` then 3.5. `App.tsx` `rows` memo: when
  `filter === 'want'`, re-sort by `quickWinScore` desc with the chosen sort as
  the tiebreak, + a "Quick wins first — shorter, higher-rated books up top."
  hint line. No segmented control — the reorder + hint was enough.

**Done:** `authorAffinity` + `quickWinScore` unit-tested; viewer 167 + build +
lint green. Backend untouched. Live effect needs the reading sidecar (already
v2/v3) + ratings, which are there.

---

## Part C — reading goal + stats — SHIPPED 2026-09-10

Live-verified query: `me { goals { goal progress metric start_date end_date } }`.
James: `{goal: 50, progress: 48.0, metric: "book", start_date: "2025-12-31",
end_date: "2026-12-30"}`. **`progress` is Hardcover's own count (all books,
not just library ones)** — exactly what to show, so the sidecar uses it as-is
(the spec's "compute it yourself" was wrong: the sidecar only sees owned
books, which would undercount badly).

- Backend: `hardcover_reading_service.fetch_goal()` — companion fn, `_GOAL`
  query, picks the `book`-metric goal whose date range covers today, year =
  `end_date[:4]`. `build_reading_payload(session, rows, reader, goal=None)` →
  sidecar `goal: {year, target, progress}` (key omitted when None).
  `regenerate_reading` calls `fetch_goal()`. **`READING_VERSION` → 3.**
- Viewer: `reading.ts` `Reading.goal: ReadingGoal | null` + `normaliseGoal` +
  `goalPace(goal, now?)` (books ahead/behind an even year-long pace).
  `components/ReadingStats.tsx` — `<ReadingGoalBar>` (slim clickable line on
  the home above the marquees: "2026 reading goal — 48 / 50 · on track" + a
  thin bar) and `<ReadingStatsScreen>` (C2).
- C2 stats screen — **no new call**, all from `readingStats(reading, rows)` in
  `lib/readingStats.ts`: read this year / all-time, pages this year, this vs
  last month, avg rating you give, a 1–5★ histogram, top-5 authors + series,
  longest book this year. Labelled "counts books in this library **and** on
  the shelves" (the sidecar can't see unowned reads). Nav: "Reading stats" in
  the header menu (when `reading.books` non-empty or a goal is set).
- Tests: backend `fetch_goal` (current-goal pick, no-book-goal → None) +
  `build_reading_payload` goal passthrough/omit; viewer `goalPace` +
  `normaliseGoal` + a full `readingStats.test.ts`. Backend 678 + corpus green;
  viewer 177 + build + lint green. **Live: sidecar v3, goal 48/50.**

### C1 — goal progress (original notes)
- **Backend** `hardcover_reading_service` — a second query
  `me { goal: ... }` for the current-year reading goal (books target). Confirm
  the field — might be `goals(where: {...})` with a `goal` count + `progress`,
  or derivable. Fold the number into `fetch_reading`'s return (or a small
  companion fn) and into `build_reading_payload` → sidecar
  `goal: { year, target, read }` (read = count of `status == read` rows with a
  `readDate` in the current year — compute from the rows we already have, don't
  trust a server `progress` field blindly).
- Sidecar → **v3**. Viewer `reading.ts` `Reading.goal?: {year, target, read}`.
- **Viewer** — a slim progress line on the home screen when `goal` is set:
  *"2026 reading goal — 23 / 50 books · 4 ahead of pace"* with a thin bar.
  Pace = `target * (dayOfYear / 365)`. Attribute to `reading.reader`.

### C2 — reading stats page
- New `<ReadingStatsScreen>` (nav button next to Activity / Wishlist, gated on
  `reading.books` non-empty). Everything derives from the reading sidecar +
  the library rows already in memory — **no new Hardcover call**:
  - books read this year / all-time (from `readDate`)
  - pages read this year (`Σ meta.pages` over read-this-year rows that have it)
  - ratings distribution (a little histogram, 0.5–5)
  - most-read author, most-read series
  - longest book read; average rating you give
  - "this month vs last month" count
- Purely presentational; reuse the `ActivityScreen` layout shell.
- Consider adding read-date granularity to the sidecar if `readDate` is
  year-only for some rows (Hardcover `last_read_date` is a full date — keep it).

**Done when:** goal line shows on the home screen with correct pace maths;
stats screen renders from the sidecar with no extra fetch; sidecar v3; the
year/pages/rating derivations are unit-tested (they're the bug-prone bit);
backend test for the goal query mapping; build + lint green.

---

## Part D — followed authors → release priority

**Extends `prompts/27`.** The release strips currently rank by *inferred*
author interest (`readingProfile` = how many of their books you've read). An
explicit **follow** is a stronger signal.

- **Backend** `hardcover_new_releases_service` (or the reading service — pick
  the one that already has a `me {` query) — pull `me { followed_authors { ... } }`
  (confirm field; might be `user_followed_authors`). Store the set of followed
  author names/ids in the new-releases sidecar (`followedAuthors: string[]`) or
  the reading sidecar — whichever the release feed already reads.
- **Viewer** `App.tsx` — in the `recentReleaseFeed` / `upcomingReleaseFeed`
  sort, bump `tier` for a followed author above the read-count tiers
  (`followed` → tier 4, then `min(readCount, 3)`).
- Small: also show a `following` dot on the `<ReleaseCard>` for a followed
  author's book.

**Done when:** a followed author's upcoming book sorts to the front of the
strip ahead of a heavily-read-but-unfollowed author; sidecar carries the list;
build + lint green.

---

## Part E — community lists

Public Hardcover lists ("Best Cozy Fantasy 2025", "If you liked Dungeon
Crawler Carl"). **Catalogue data — shared-viewer safe.** Two payoffs:

### E1 — "on N lists" signal
- **Backend** `hardcover_recs_service` — the first per-book call can also ask
  `book { lists_count }` (or count via `list_books_aggregate`). One extra
  field, no extra call. → `meta.listsCount`. Index v6 (fold into Part A's bump
  if done together) / v7.
- **Viewer** — `BookRow.tsx` a muted *"on 14 lists"* line in the expanded
  section; optional sort "most-listed".

### E2 — list-based discovery
- **Backend** a new `hardcover_lists_service` + `bookbrain-lists.json` sidecar:
  for each **series or author the library knows**, find the top ~3 public
  lists featuring their books (`lists(where: {list_books: {book: {id: {_in: ...}}}}, order_by: {followers_count: desc})`),
  then pull each list's books. Cross-reference:
  - books on those lists **you own** → *"you own 6 of the 20 books on 'Best
    Grimdark'"* (a completion nudge)
  - books **you don't** own → wishlist candidates, tagged with the list name.
  - Cap aggressively (lists are big); dedupe by book.
- **Viewer** — a **"From lists you'd like"** section on the Wishlist screen
  (mirror the `prompts/30` "From your Hardcover want-to-read" card), and/or a
  strip on the home screen. Each candidate → one-tap Request, `from <list name>`.
- Nightly step (token-gated, never-fails), + `POST /api/library/lists`.

**Done when:** E1 shows list counts on books; E2 sidecar builds and the
Wishlist screen shows list-sourced candidates filtered against owned + already
listed; backend tests for the list cross-reference; build + lint green.

---

## Part F — Hardcover Prompts ("your library answers")

Hardcover's **Prompts** feature = a community question with book answers
("Books with an unreliable narrator", "Cozy fantasy, no romance"). Public →
shared-viewer safe.

- **Backend** a new `hardcover_prompts_service` + `bookbrain-prompts.json`
  sidecar: pull the top ~40 prompts by answer/follower count
  (`prompts(order_by: {..._count: desc}, limit: 40) { question prompt_books { book { id } } }`),
  keep only the prompts where **≥ 2 books you own** appear in the answers, and
  store `{ question, ownedBookIds: [driveId], hardcoverUrl }` (resolve
  Hardcover book id → drive id with the same ISBN/title match
  `build_reading_payload` uses — factor that matcher out into a shared helper).
- Nightly step + `POST /api/library/prompts`.
- **Viewer** — a **`<PromptsScreen>`** (nav button): a list of questions, each
  expandable to the covers of the owned books that answer it, click → the book.
  A "surprise me" shuffle. Low, browsable, fun.
- The matcher helper (Hardcover-book-id → owned drive id) is reused by Part E
  and Part G — extract it once (`library_index_service._match_hardcover_books`
  or similar) with tests.

**Done when:** prompts sidecar builds with only "your library answers" the
question; the screen renders; shared matcher helper extracted + unit-tested;
build + lint green.

---

## Part G — "what the community's reading now" strip

- **Backend** — `books_trending` (confirm the field; Hardcover has a trending
  query, possibly `books_trending(from: $date, limit: 30)` or a
  `trending_books` view). Store `bookbrain-new-releases.json`'s sidecar gets a
  `trending: [{title, author, isbn13, ...}]` array (reuse that file — it's the
  same "discovery feed" shape) OR its own sidecar if cleaner.
- **Viewer** — a fourth `<ReleaseMarquee>` "Trending on Hardcover", **gated on
  a setting** (`showTrending`, default off — same treatment as Part 3's global
  strip in `prompts/27`). Covers → `<ReleaseCard>` → Request (or "In library"
  badge if owned, using the shared matcher).

**Done when:** trending strip shows behind the setting; owned trending books
show an "In library" badge; build + lint green.

---

## Part H — edition metadata gap-fill

`prompts/26` Part B pulls `pages` for the recs pass. Widen it:

- **Backend** — wherever the per-book Hardcover call runs
  (`hardcover_recs_service`), also read the best edition:
  `editions(order_by: {users_count: desc}, limit: 1) { pages release_date audio_seconds physical_format }`.
  - `meta.pages` — already have; keep the edition value as a fallback when the
    book-level `pages` is null.
  - `meta.published` — first publication year, if the book has none.
  - `meta.audioHours` — `round(audio_seconds / 3600, 1)` when present → a
    *"~14h audio"* hint.
- **Description-service tie-in:** none needed; this is metadata only.
- **Viewer** — the expanded row's stats line (`meta.pages · literaryType ·
  ratingsCount`) gains `published` and `audioHours` when set.
- Skip the "you own the 2011 edition, there's a 2020 revised" idea for now —
  BookBrain doesn't track *which* edition a file is, so it can't compare.
  Note that in the commit as deliberately out of scope.

**Done when:** books missing a page count / pub year get one from the edition;
audio hint shows where Hardcover has it; `_book_meta` tests cover the edition
fallbacks; build + lint green.

---

## Part I — reading-progress two-way sync (the big one)

Resurrects `prompts/17` §E (cross-device reading position), which was dropped
because a Drive sidecar felt like overkill. Hardcover already stores reading
progress — use it as the backing store. **Do this last; check in with James
before starting — it's the largest part and the most licence-sensitive.**

### What Hardcover has
`user_book_reads` (per read-through): `progress_pages` / `progress_seconds`,
`started_at`, `finished_at`, `edition`. Confirm write mutations exist
(`insert_user_book_read` / `update_user_book_read`) — `prompts/30` Phase 3
proved status writes work; progress writes are the open question.

### Design
- **Viewer → Hardcover:** the reader (`Reader.tsx`) already writes
  `readingProgress.ts` (`setProgress(id, cfi, percent)`, localStorage). On
  reader close, if the book matched a Hardcover book, **queue a progress
  change** to `bookbrain-reading-pending.json` (extend the `prompts/30` Phase 3
  queue — add `progressPercent` / `progressPages` to `ReadingChange`, or a
  parallel `progress` queue).
- **Hardcover → viewer:** `regenerate_reading` already pulls `user_books`;
  also pull the latest `user_book_reads` progress per book → sidecar
  `books[driveId].progress = { percent, pages, updatedAt }`.
- **Viewer merge:** `readingProgress.ts` `getProgress` falls back to the
  sidecar's `progress` when local is absent or older (`updatedAt` compare).
  The "Continue reading" strip and the reader's resume point both benefit.
  **Position (CFI) stays local** — Hardcover only has a page/percent, which
  can't seek an EPUB precisely; use it to pick the *chapter* and page from
  the start of it, or just show "~62% (from Hardcover)".
- **Backend `apply_pending`** — extend to handle a progress entry:
  `update_user_book_read` with `progress_pages` (convert percent → pages via
  `meta.pages`) or `progress_seconds`. Idempotent (skip if within ~1%).

### Risks / notes
- Percent↔page conversion is lossy; never *reduce* Hardcover progress from a
  stale local value — only advance it.
- If progress writes turn out not to be in the API, ship the **read-only**
  direction (Hardcover → viewer resume hint) and note the write side as
  blocked.
- Big test surface: the merge precedence, the percent→page maths, the
  idempotence guard.

**Done when:** finishing ~60% of a book in the viewer shows up as progress on
Hardcover (or, if writes are unavailable, Hardcover progress shows as a resume
hint in the viewer); merge precedence unit-tested; `apply_pending` progress
path tested; full suite + build + lint green; **browser-verified** (this one
needs a real round-trip).

---

## Constraints / gotchas (all parts)

- **Backend restart on Windows** — no `--reload`; kill by port
  (`netstat -ano | grep :8000` → `taskkill //F //PID`, *not* the `ps` pid) —
  see `project-bookbrain-reading-status`.
- **API cost** — none of this touches Anthropic. Hardcover paid tier
  50k/day; the per-book calls already run nightly, the new `me {` /
  `lists` / `prompts` / `trending` queries are a handful more. Fine.
- **Never a Hardcover cover image URL** (DMCA — `prompts/25`). OL-by-ISBN only.
- **Sidecar versions** — bump `INDEX_VERSION` once if Parts A+E1+H land
  together (they all touch `meta`); bump `bookbrain-reading.json` to v3 for
  Part C. The viewer must tolerate an older sidecar (missing field → default).
- **Shared matcher** — Parts E, F, G all need "Hardcover book id → owned drive
  file id". `build_reading_payload` has the logic (ISBN then `_norm_key`).
  Extract `_match_hardcover_books(session, hc_books) -> dict[hc_id, drive_id]`
  with tests **before** Part E, reuse it after.
- **One commit per part.** Update `prompts/README.md`, `ROADMAP.md`, and the
  `project-bookbrain-hardcover` + `project-bookbrain-reading-status` memories
  as each lands. Deploy is automatic on push (viewer) — the sidecars need a
  `POST` refresh + `POST /api/library/index` after the backend restart.
- **Verify each Hardcover query live before building the part** (scratch script,
  `.env` token). The field names in this doc are unverified.
