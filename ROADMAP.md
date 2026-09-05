# Roadmap

Loose backlog — not commitments, just the ideas worth not forgetting.

## Later / maybe

- **Four review follow-ups (2026-09-06)** — briefs in `prompts/`, one per
  session: (1) ship series-merge *(done, see below)*, (2) scheduled nightly
  runs, (3) write resolved metadata + cover into the EPUB, (5) bulk
  re-identify audit.
- **Series merge — auto-create a `library_rule` (series_alias)** on apply, so
  the next scan that phrases the merged-away name the old way gets corrected
  by `find_rule_match` instead of re-forking the Series row (and Drive
  folder) all over again. Today a re-fork just means running the merge again.
- **Library Audit similarity threshold is loose** — a real ~2200-book library
  throws ~780 series + ~101 author clusters, many false positives sharing one
  generic word ("Wild" vs "Wild Cards", a chronicle vs a chronicle). The
  dismiss feature covers it, but tightening `_SIMILARITY_THRESHOLD` /
  weighting the distinguishing word would cut the noise.
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
## Done (kept for context)

- **Library Audit: dismiss clusters + in-place series merge** (2026-09-06) —
  Investigate a "possibly split series" cluster (Claude picks canonical from
  the existing names only), preview the exact Drive move plan, Apply (moves
  files/folders, undoable Operation per file, deletes emptied series,
  idempotent). Dismiss/restore any cluster. Also fixed `list_folders`
  truncating at one page (433-folder root → duplicate folders).
- **CI: run `npm run lint` + `npm test` for library-viewer** before the
  build/deploy steps (2026-09-05) — was blocked on the pushing token
  lacking `workflow` scope; fixed with `gh auth refresh -s workflow`.

- Correct an already-organised book — `POST /api/files/{id}/correct` +
  a "Correct" button on every identified Library row (2026-09-04).
- Tap an author / series to filter to it in the library-viewer.
- Cover job: `.nocover` markers so no-cover EPUBs aren't re-downloaded.
- Wishlist (backend / admin app) — free-text → Claude+GoogleBooks resolve → list, auto-ticked when the book is imported.
- Wishlist (library-viewer) — Google Books search + pick, stored as
  `bookbrain-wishlist.json` in the Drive library folder (syncs across
  devices), no Claude / no API credits, auto-ticks on library match
  (ISBN then fuzzy title+author) (2026-09-04).
