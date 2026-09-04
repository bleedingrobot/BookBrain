# Roadmap

Loose backlog — not commitments, just the ideas worth not forgetting.

## Later / maybe

- **Wishlist auto-tick promptness** — reconcile only runs on GET /wishlist (backend) or on opening the Wishlist screen (viewer); a scan/organize hook would tick items off without opening the page (skipped for now to avoid the network-in-tests hang; would need a mock guard).
- **Wishlist (viewer): concurrent-save dedup** — `persist` guards the Drive
  file id with a ref, but two saves that start before the first create
  returns could still both create `bookbrain-wishlist.json`. Fine for
  single-user click rates; would need a write queue to be truly safe.
- **library-viewer: "new since last visit"** — stamp a last-opened time,
  surface books added since.
- **Populate `Book.description` for the ~950 still-blank books** — the
  free-source backfill (`POST /api/library/descriptions`) filled 109; the
  rest need the `ai=true` path (Claude), which costs credits.
- **CI: run `npm run lint` + `npm test` for library-viewer** — the deploy
  workflow only builds. (Blocked on the pushing token having `workflow`
  scope.)

## Done (kept for context)

- Correct an already-organised book — `POST /api/files/{id}/correct` +
  a "Correct" button on every identified Library row (2026-09-04).
- Tap an author / series to filter to it in the library-viewer.
- Cover job: `.nocover` markers so no-cover EPUBs aren't re-downloaded.
- Wishlist (backend / admin app) — free-text → Claude+GoogleBooks resolve → list, auto-ticked when the book is imported.
- Wishlist (library-viewer) — Google Books search + pick, stored as
  `bookbrain-wishlist.json` in the Drive library folder (syncs across
  devices), no Claude / no API credits, auto-ticks on library match
  (ISBN then fuzzy title+author) (2026-09-04).
