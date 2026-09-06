# Task 17 — Read EPUBs in the library-viewer

Add an in-browser EPUB reader to `library-viewer/` so the family can actually
*read* a book, not just browse the shelf and send it to a Kobo. **EPUB only** —
no MOBI/PDF/CBZ/CBR, no format fallbacks. James was explicit about that.

This is a `library-viewer`-only change (plus one small optional backend field —
see §D). It's the family-facing static app, so it ships by pushing to `main`
(GitHub Actions deploys it). No new npm dependency: `foliate-js` is **vendored**.

## First read

- `prompts/README.md` — shared context, the three-app layout, the
  `library-viewer` build/test/lint commands, the "push to main deploys it" rule.
- `ROADMAP.md` § "Viewer as a reading tool, not just a shelf" — this task is the
  first, foundational piece of that cluster (reading position is the first
  instance of per-person reading state; §C's "Continue reading" strip is the
  flipped-around `seriesGaps.ts` idea).
- Memory `project-bookbrain-metadata-sidecar` (the viewer's shape, what's
  shipped) and `project-bookbrain` (repo layout: real checkout, running dev
  servers, check `git status` in `library-viewer/` first).
- The viewer files you'll build on:
  - `src/lib/drive.ts` — `downloadFile()` already does the exact
    `fetch(.../files/{id}?alt=media, { Authorization: Bearer <token> })` call
    you need; `DriveFile` is `{ id, name, ... }`. `readJsonFile` / `writeJsonFile`
    are the Drive-sidecar helpers.
  - `src/lib/books.ts` — `BookRow` (`id` is the Drive file id, `file` is the
    `DriveFile`, `filename` carries the extension).
  - `src/lib/covers.ts` — the pattern for an authenticated Drive blob fetch with
    an in-memory cache + `URL.createObjectURL`.
  - `src/lib/wishlist.ts` / `src/lib/activityLog.ts` — the read-modify-write
    Drive-JSON-sidecar pattern (only needed if you do the §E stretch).
  - `src/lib/viewerIdentity.ts` — `getViewerName()`, the honesty-based
    per-browser name (no OAuth identity scope). Relevant only to §E.
  - `src/App.tsx` — **no router**; screens are `useState` booleans
    (`showWishlist`, `showActivity`, …). The reader is another screen in that
    style: a `readingBookId: string | null` in App state, or a `#read/<id>` hash
    if you want back-button support. Match what's there — don't add
    react-router.
  - `src/components/BookRow.tsx` — where the per-book action buttons live
    ("Send to Kobo", download). The "Read" button goes here.

## Why

The viewer is "browse what we own". The payoff of a personal library is
*reading* it. Everything needed is already in place — OAuth with `drive` scope,
authenticated Drive downloads, the sidecar-JSON state pattern, a PWA shell,
per-person identity. What's missing is a renderer and somewhere to keep your
place.

---

## A. Vendor foliate-js

[`foliate-js`](https://github.com/johnfactotum/foliate-js) — MIT, the engine
behind the Foliate desktop reader, actively maintained, pure ES modules, no
build step. Its npm packaging is patchy, so **vendor it**:

- Copy the repo (or a `git subtree`) into `library-viewer/src/vendor/foliate/`,
  **pinned to a specific commit** — record the commit SHA in a
  `src/vendor/foliate/VERSION` file and in the commit message.
- You only need the EPUB path: `view.js`, `epub.js`, `epubcfi.js`,
  `paginator.js`, `overlayer.js`, the `vendor/` zip helpers it imports, and
  whatever else those pull in transitively. **Do not** vendor `pdf.js`,
  `mobi.js`, `comic-book.js`, `fb2.js` or their wasm — EPUB-only means no wasm at
  all.
- `foliate-js` unzips in a Web Worker (`new Worker(new URL(...))`). Vite 8
  handles that natively; confirm the worker + its chunks land in `dist/` after
  `npm run build` and that the deployed (GitHub Pages, non-root base path) build
  can load them. If the base-path bites, that's a `vite.config` `base` / worker
  `format: 'es'` fix.
- `oxlint` will lint the vendored files — add `src/vendor/` to `.oxlintignore`
  (or the oxlint config's ignore list) so third-party code isn't held to the
  project's rules.
- License: keep foliate-js's `LICENSE` file in the vendor dir; add a line to the
  viewer's README noting the vendored dependency + commit.

## B. The reader screen

A full-screen reader. Opening a book:

1. **Get the bytes.** `fetch(.../files/{fileId}?alt=media)` with the bearer
   token (reuse the `downloadFile` fetch, factor the common part into a
   `fetchDriveBlob(token, fileId)` helper). Show a loading state — some epubs
   are a few MB.
2. **Cache the bytes** in IndexedDB keyed by `fileId` (new
   `src/lib/bookCache.ts` — a thin wrapper, no `idb` dependency, raw
   IndexedDB is fine for one object store). On the next open, serve from cache
   → instant, and works offline (the app is already a PWA). Bound the cache:
   LRU eviction at ~300 MB total or ~20 books, whichever first; expose a
   "Downloaded for offline (N)" count + a "Clear" in Settings
   (`SettingsForm.tsx`), mirroring the existing "Clear cover cache" /
   "Clear library cache" controls.
3. **Render** with `foliate-js`: create a `<foliate-view>`, `open()` the blob.
   Paginated (columned, book-like) — not scrolled. Page turn on: tap/click the
   left & right thirds of the screen, `←`/`→` and `PageUp`/`PageDown`, and
   swipe. A tap in the **middle third** toggles the chrome (top bar + bottom
   bar); default to chrome-hidden after a second of inactivity.
4. **Controls** (bottom bar or a settings sheet): font size (−/+), font family
   (publisher / serif / sans), line height, margin width, and theme
   (light / sepia / dark). Persist **per device** to `localStorage`
   (`bookbrain.readerPrefs`) — these are screen-specific, they don't belong in
   a synced sidecar. Apply via foliate's style hooks (`renderer.setStyles` /
   the `--foliate-*` CSS custom properties — check the current API against the
   pinned commit).
5. **TOC**: a drawer from the top bar, built from foliate's `book.toc`; tapping
   an entry navigates; highlight the current chapter.
6. **Progress**: foliate can generate a locations index for the whole book
   (paginate-once, ~1–2 s for a novel — do it in the background after first
   render, show `%` as soon as it's ready). Show current `%` and
   "page N of ~M" in the bottom bar, and a draggable progress slider. No
   time-in-minutes estimate in v1 (that needs word count — §D).
7. **Save position** on every relocate event, **debounced ~1 s**, to
   `localStorage` via a small `src/lib/readingProgress.ts`:
   `{ [fileId]: { cfi: string, percent: number, updatedAt: number } }`.
   On open, restore to the saved `cfi` if present. Keep this module's API
   sidecar-ready (`getProgress` / `setProgress` / `allProgress`) so §E can swap
   the backing store without touching the reader.
8. **Close** returns to the exact scroll position in the book list the reader
   was opened from (App already tracks `pendingScrollId` — reuse it).

Errors: a corrupt/non-EPUB file, an expired token (`isAuthError`), an offline
open with nothing cached — each shows a plain message with a "Back" and, where
it makes sense, a "Download instead" (the existing `downloadFile`). Never a
blank screen.

Wire the **"Read"** button into `BookRow.tsx`, shown only when
`row.filename` ends `.epub` (case-insensitive). Everything else keeps today's
download/send affordances unchanged.

## C. "Continue reading" on the home screen

A horizontal strip at the top of the library list (above or beside
`RecentMarquee`), only rendered when there's something in it:

- Source: `readingProgress` entries with `0 < percent < 0.98`, joined to the
  current `BookRow`s (drop entries whose file is no longer in the library),
  sorted by `updatedAt` desc, cap ~12.
- Each item: cover + title + a thin progress bar + `%`. Tap → opens the reader
  at the saved position.
- A book that reaches ≥ 98% drops off the strip automatically (treat as
  finished). No explicit "mark finished" control in v1 — that's §E.

This is the change that turns the app from a catalogue into a reading app; ship
it in the same PR as §B.

## D. (optional) word count → time-left estimate

Skippable for v1 — without it the reader shows `%` and page counts, just not
"~45 min left". If you do it:

- Backend: `scan_service` already walks the full spine for
  `_extract_text_snippet` (Stage D). Have it also count words there and store
  the total. Cheapest home: a new nullable `books.word_count` column (plain
  `op.add_column`, one Alembic migration — `tests/test_migrations.py` enforces
  it) set on the identify path; a one-off `scripts/backfill_word_counts.py`
  (parse each organised epub once, no AI) for the existing ~2200.
- `library_index_service`: add `"wordCount"` to each entry in the sidecar.
- Viewer `libraryIndex.ts`: parse it through; reader shows
  `~round((wordCount * (1 - percent)) / 250)` min left.

If it's not in this PR, add it to ROADMAP as a follow-up rather than half-doing
it.

## E. cross-device reading state — DROPPED

> **2026-09-07: James decided not to pursue this.** Reading position stays
> per-device (`localStorage`). A "sync from Kobo" button was considered and
> also dropped. The rest of this section is kept for context only.

~~Only if §B/C land clean and there's appetite.~~ A `bookbrain-reading-state.json`
Drive sidecar, same read-modify-write + concurrent-save guard as
`wishlist.ts`:

```
{ "version": 1,
  "byUser": {
    "<viewerName>": {
      "<fileId>": { "cfi": "...", "percent": 0.42,
                    "status": "reading" | "finished" | "want",
                    "updatedAt": "<iso>" } } } }
```

Swap `readingProgress`'s backing store from `localStorage` to this (keeping
`localStorage` as the offline write-buffer, flushed on reconnect). Adds
position sync across a person's devices + manual want/reading/finished toggles,
and a "finished a series book" signal `seriesGaps.ts` can use for "you own #3,
not #4 → add to wishlist". Big enough to be `prompts/18`; note it there if you
stop after §C.

## Gotchas / constraints

- **No new npm dependency.** `foliate-js` is vendored. If something genuinely
  can't be done without a package, stop and flag it rather than adding one
  quietly — the viewer deliberately ships with only `react` + `tailwind`.
- **GitHub Pages base path.** The viewer deploys to a non-root path. Web
  Worker URLs, dynamic `import()` of vendored modules, and any asset the
  reader loads must resolve under that base — test the actual `npm run build`
  output, not just the dev server.
- **Token lifetime.** `useLibrary` silently renews the Drive token ~2 min
  before expiry. A long reading session is fine (bytes are already downloaded
  / cached), but if you re-fetch mid-session use the *current* token from the
  hook, not a captured one.
- **Large / illustrated epubs** (30–100 MB) exist. The download must stream and
  the cache must not blow past its bound — evict oldest.
- **`.kpub`** — out of scope. The "Read" button is `.epub`-only; `.kpub` books
  keep the download button. (One line to also accept `.kpub` later if wanted —
  it's just an epub — but not now.)
- **PWA / offline.** Reading a cached book with no network must work end to end
  (bytes from IndexedDB, prefs + progress from `localStorage`, no Drive call).
  The service worker must not break on the vendored worker script.
- **iOS Safari:** the paginated view's horizontal swipe fights Safari's
  back-swipe near the screen edge — guard the edge zones (`touch-action`, or
  keep the turn taps inset).
- **Don't regress the existing screens.** The reader is additive; `BookList`,
  wishlist, activity, Kobo-send all behave exactly as before when the reader is
  closed.
- **Tests:** `bookCache`, `readingProgress`, and the "Continue reading"
  selection logic are plain modules — unit-test them (vitest, like
  `seriesGaps.test.ts`). The foliate integration itself is manual-QA.

## Acceptance

- "Read" on an EPUB opens a full-screen paginated reader; page turns by
  tap/key/swipe; font size + theme + TOC work.
- Close and re-open the same book → lands back where you left off.
- Second open of a book is instant and works with the network off (DevTools
  offline).
- The home screen shows a "Continue reading" strip with any part-read book,
  newest first; tapping resumes.
- A finished (≥98%) book leaves the strip.
- Non-EPUB books are unchanged — no "Read" button, download/send as before.
- `cd library-viewer && npm run build && npx vitest run && npm run lint` all
  green; the deployed (base-path) build loads the reader.

## Ship it green

```
cd library-viewer && npm run build && npx vitest run && npm run lint
# only if §D touched the backend:
cd backend && pytest
cd backend && pytest -m corpus
```

One or two commits (vendor foliate-js as its own commit; reader + Continue
reading together). Push to `main` — that deploys the viewer.

Then:

- `ROADMAP.md` — mark the EPUB reader + "Continue reading" done under "Viewer as
  a reading tool"; leave ratings / NL search open. Per-person reading state +
  §E cross-device sync + Kobo round-trip are **dropped** (James, 2026-09-07).
- `prompts/README.md` — add row 17 (done), note it's viewer-only + vendored dep.
- `library-viewer/README.md` — the vendored `foliate-js` + its pinned commit;
  a line on the reader + offline cache + where prefs/progress live.
- Memory: update `project-bookbrain-metadata-sidecar` (viewer now reads books,
  not just lists them) and add a short `project-bookbrain-viewer-reader` note
  (foliate-js commit, cache bounds, where progress lives, §D/§E status).
