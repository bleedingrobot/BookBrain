# Roadmap

Loose backlog — not commitments, just the ideas worth not forgetting.

## Later / maybe

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
