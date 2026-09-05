# Roadmap

Loose backlog — not commitments, just the ideas worth not forgetting.

## Later / maybe

- **Four review follow-ups (2026-09-06)** — briefs in `prompts/`, one per
  session: (1) ship series-merge *(done, see below)*, (2) scheduled nightly
  runs *(done, see below)*, (3) write resolved metadata + cover into the EPUB
  *(done, see below)*, (5) bulk re-identify audit.
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

- **Write resolved metadata + cover into the EPUB** (2026-09-06) — reverses
  SPEC §3's "no EPUB metadata writing". `app/providers/epub/writer.py`
  (`write_metadata`, pure fn) rewrites the OPF: one `dc:title` / `dc:creator`,
  the legacy `calibre:series` pair **and** EPUB 3 `belongs-to-collection`
  (Kobo reads the legacy tags), embeds a cover only when the epub has none,
  copies every other zip entry through byte-for-byte, keeps `mimetype`
  first + stored. Separate opt-in "Fix embedded metadata" button on the
  Library page + `POST /api/library/embedded-metadata?dry_run=` — not wired
  into organize. Resumable via `files.embedded_metadata_key`; the
  pre-rewrite hash goes to `files.original_sha256` and is matched alongside
  `sha256` in dup-detection + sticky corrections. In-place Drive update
  (same `drive_file_id`), logged as a `write_metadata` operation, **not
  undoable** (no original bytes kept; Drive revision history is the
  fallback). Follow-ups worth noting:
  - `_clear_series` removes *any* EPUB 3 `collection-type` / `group-position`
    meta, not just ones refining our series id — fine for this
    Calibre-origin library (flat single series), would over-clear a nested
    sub-collection.
  - Output OPF is `<opf:package>`-prefixed (ElementTree can't keep a default
    namespace without dropping `opf:` *attributes* elsewhere) — valid EPUB,
    reads fine in ebooklib / Calibre / Kobo, but visually different from the
    original.
  - Cover source for no-cover epubs is the 320px `covers/<id>.jpg`
    thumbnail — a shelf thumbnail, not full-res.

- **Scheduled nightly runs** (2026-09-06) — Settings toggle + hour. Two layers
  sharing `app/jobs/nightly.py::run_nightly`: an in-process APScheduler job
  (FastAPI lifespan) and a standalone `python -m app.jobs.nightly` entrypoint a
  Windows Scheduled Task calls (`backend/scripts/register-nightly-task.bat`).
  One pass = Torrents pull → scan → threshold auto-organize → covers → index;
  never auto-resolves a review or duplicate. Dead token → clean "reconnect in
  Settings", no traceback. `job_runs` table is the audit trail + a coarse
  "a run is active" guard; Dashboard shows the last run. This closes SPEC's
  "manual scan only, designed so a scheduler can call it later" (§ "No webhooks").
- **.cbr (RAR) comic support** (2026-09-06) — handled exactly like .cbz (kept
  as-is, never converted; reads ComicInfo.xml). Container is picked by magic
  bytes not extension, since `.cbr`/`.cbz` are widely mislabelled. RAR reading
  shells out to 7-Zip (auto-detected, or `SEVEN_ZIP_BINARY`); no 7-Zip ⇒ the
  file lands in `unidentified` with a clear reason.
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
