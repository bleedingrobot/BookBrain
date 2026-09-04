# Roadmap

Loose backlog — not commitments, just the ideas worth not forgetting.

## Next up

### Correct an already-organised book (highest impact)

Today, if the AI mis-identifies a book and it lands `organised` with high
confidence (e.g. *Scion* filed as *The Hierarchy #2*), there is **no UI to
fix it** — `review_service.correct()` needs a *pending* `reviews` row, and
the Library page only offers "remove file". Fixing one currently means
hand-editing the SQLite database.

Wanted: a "Re-open for review" / "Correct" action on organised rows in the
Library page that:

- reopens the file for review (or opens the correct dialog directly),
- lets you set title / author / series / number,
- writes the sticky `reviews` row (status `corrected`, keyed by `sha256`
  per SPEC §1) and re-points `file.book_id`,
- flips status back to `inbox` so the next Organize run moves/renames it,
- deletes the stale `ai_decisions` cache row for that hash.

Most of the plumbing exists in `review_service.correct()`; the gap is a
route to trigger it for a file that isn't already in the review queue, plus
the frontend affordance.

## Later / maybe

- **Populate `Book.description`** — the column is dead; ~half the library
  has no EPUB `<dc:description>`. Fill from a provider or Claude.
- **library-viewer: "new since last visit"** — stamp a last-opened time,
  surface books added since.
- **library-viewer: tap a badge to filter** — author / series pill →
  filtered list.
- **Cover backfill: skip known no-cover books** — the ~1% of EPUBs with no
  extractable cover get re-downloaded on every `Generate covers` run.
- **CI: run `npm run lint` + `npm test` for library-viewer** — the deploy
  workflow only builds. (Blocked on the pushing token having `workflow`
  scope.)
