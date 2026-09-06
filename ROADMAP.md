# Roadmap

Loose backlog — not commitments, just the ideas worth not forgetting.

## Later / maybe

- **Four review follow-ups (2026-09-06)** — briefs in `prompts/`, one per
  session: (1) ship series-merge *(done, see below)*, (2) scheduled nightly
  runs *(done, see below)*, (3) write resolved metadata + cover into the EPUB
  *(done, see below)*, (5) bulk re-identify audit *(done, see below)*. All four
  shipped.
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

- **AI spend guard rails on descriptions + rebuild** (2026-09-06, review batch #2
  / finding 11, P2) — `POST /api/library/descriptions?ai=true` fanned out one
  `describe()` per description-less book (~950) with no cap or estimate;
  `POST /api/library/rebuild` re-identified every unknown file (~2200 after Clear
  Library) with no warning. Now: `ai_description_cap` (200) rations the model
  blurbs per run (free provider pass stays uncapped; a re-run continues);
  `GET /api/library/descriptions/estimate` and `GET /api/library/rebuild/estimate`
  return `~N, ~$X` with **zero** AI calls; `Library.tsx` shows the estimate and
  needs a second click before either AI run. Per-call `$` figures are padded
  constants in `config.py`. Rebuild estimate degrades to "couldn't estimate,
  proceed?" if the Drive listing fails.

- **Write locks unified onto one shared commit serialiser** (2026-09-06, review
  batch #2 / finding 09, P2) — `conftest._reset_shared_singletons` reset five
  locks but missed `OrganizeService._write_lock` (an instance attr on the module
  singleton), a latent "Lock bound to a different event loop" for the next test
  to exercise the organize singleton. Rather than just add a sixth reset, organize
  and series-merge commits now take `book_repository.get_book_write_lock()` — the
  same lock scan and review already use. `OrganizeService._write_lock` /
  `series_merge_service._write_lock` and their `reset_*` helpers are deleted;
  cross-service commits (a manual organize overlapping a nightly scan's tail, a
  merge overlapping either) now actually serialise. `metadata_writeback`'s lock
  (longer critical section) and `nightly`'s ("run already active" guard) are left
  alone. Concurrency tests unchanged and green.

- **Series-merge operations are honestly non-undoable** (2026-09-06, review batch
  #2 / finding 10, P2) — `apply_series_merge` logged each moved file as
  `Operation(move_and_rename)`, which `undo_operation` treated as auto-undoable
  and Activity showed an Undo button for — but undoing one moves the file back to
  the folder the merge just deleted, with `book.series` still pointing at
  canonical. New `OperationAction.series_merge` (migration `f0e1d2c3b4a5`,
  batch-alter + a data UPDATE converting existing `move_and_rename` rows whose
  `reason` starts `series merge:`). `undo_operation` raises with a message
  pointing at the real remedy. `OperationSummary.undoable` (bool) computed from
  `_UNDOABLE_ACTIONS`; Activity gates Undo on it — which also correctly hides the
  never-worked Undo on `write_metadata` rows — and shows an action label +
  explanatory note for merges.

- **library-viewer: incremental sync dropped a file on a `parents`-less change**
  (2026-09-06, review batch #2 / finding 07, P1) — `applyChanges` computed
  `(file.parents ?? []).some(...)` and evicted the cache entry when false — which
  is true both when a file genuinely moved out *and* when Drive simply omitted
  `parents` from the change delta (it doesn't send it on every one). A still-in-
  library file then stayed invisible until the 24h auto-rebuild. Fix: treat
  `file.parents === undefined` as "no placement info" — skip, leave the cache
  entry alone — distinct from `[]`/populated (existing evict logic). Same guard
  on the folder-removal pass. New `src/lib/librarySync.test.ts`.

- **Alembic enum drift + `alembic check` gate** (2026-09-06, review batch #2 /
  finding 08, P2) — `files.status` gained `rejected` and `files.status_reason`
  gained `previously_rejected` / `same_book` in the models with no migration, so
  `alembic check` was red and the SQLite `VARCHAR(14)` was too narrow for
  `previously_rejected` (19). New migration `b2c3d4e5f6a7` (`batch_alter_table`
  recreate on SQLite, no data change, indexes verified intact). New
  `tests/test_migrations.py` runs `alembic upgrade head` + `alembic check` in a
  subprocess — schema drift now fails `pytest`.

- **Strict title match for book identity** (2026-09-06, review batch #2 / finding
  06, P0) — `book_repository.resolve_book` matched incoming titles against
  existing same-author books with the *loose* `normalize_title`, which strips a
  `:`/`;` subtitle. For the very common "`<Series>: <Book Title>`" epub title
  format that collapsed two genuinely different books onto one `Book` row;
  `detect_same_book_duplicates` then flagged one as `same_book` (hiding it from
  organize + the viewer) and the bulk "Clear duplicates" could trash it. Fix:
  new `text_match.normalize_title_strict` (keeps the full title, still folds
  case/article/trailing-parens) used **only** for the row-identity decision in
  `resolve_book`; the loose `normalize_title` stays everywhere it's used for
  confidence scoring / provider corroboration. Defense in depth: `clear_duplicates`
  now skips `same_book` rows entirely — those clear one at a time via
  `POST /api/duplicates/{id}/clear`, with a "Not a duplicate"
  (`POST /api/duplicates/{id}/unflag`, splits the file onto a fresh book) option
  on the Duplicates page, which now has a separate "Same book, different file"
  section and a corrected header. Repair for existing casualties:
  `POST /api/library-audit/repair-title-merges` + a "Repair falsely-merged books"
  button on the Library Audit → Split records tab (DB-only, no Drive/AI, idempotent).
  Accepted trade-off: "The Hobbit" and "The Hobbit: There and Back Again" now
  resolve to two rows rather than one.

- **library-viewer: drop guest/read-only mode + zero-setup defaults**
  (2026-09-06) — the `?clientId=&folderId=` share link used to set
  `readOnly: true`, which requested `drive.readonly` and hid Kobo devices,
  the wishlist and activity logging. Removed entirely: everyone now gets the
  full `drive` scope and the full UI. The trade-off James accepted: every
  person who signs in grants the (unverified, personal) OAuth app full
  read/write to their whole Drive — the scarier consent screen — but there's
  no longer a broken half-experience or per-device retyping. New
  `src/lib/config.ts` bakes the Google Client ID + library folder ID in as
  build defaults (both are public-safe), so `loadSettings()` returns a
  working config with nothing in localStorage — open the deployed URL, hit
  "Sign in", done. localStorage / a share link still override the defaults
  for a device pointed at a different library. Existing devices that used the
  old share link get silently upgraded to full access on their next sign-in
  (scope change forces a fresh Google consent). `bookbrain.readOnly` is now
  an unread orphan key; `clearSettings()` cleans it up.

- **Bulk Re-identify Audit** (2026-09-06) — `prompts/05`. A second tab on the
  Library Audit page. Where task 1 compares DB row *names*, this re-derives
  what identification would say now and diffs it against what's stored, per
  organised book. `app/services/reident_audit_service.py` +
  `GET/POST /api/library-audit/reident*`. The free pass makes **zero**
  Anthropic calls — it reconstructs the EPUB evidence + candidates from the
  stored `metadata_sources`/`book_candidates`, reuses the cached
  `ai_decisions`, recomputes the deterministic `confidence_service` score, and
  does free Google Books / Open Library lookups. Signals: series not backed by
  EPUB/candidate/provider and not human-set (the AI-invented-series case),
  provider consensus now disagreeing on title/author, stored ISBN resolving
  elsewhere, stored confidence below the auto bar, two organised books with the
  same canonical identity. Report cached as a JSON blob in `settings`
  (`reident_report_json`, carries `generated_at`), rebuilt on demand as a
  tracked in-memory job. Deep re-check = opt-in, capped at 50/run, shows a
  credit estimate first, only touches already-flagged rows, writes verdicts
  back onto the cached report (never a book row — the whole feature is
  read-only; acting on a row is the existing `/correct` flow). Per-row dismiss
  persisted in `dismissed_reident_flags`, filtered at read time like the audit
  cluster dismissals. Follow-ups worth noting:
  - `below_auto_organize` fires below `confidence_auto_flagged` (85), not 95 —
    scan deliberately auto-organizes the 85-94 tier in v1, so flagging all of
    those as "shouldn't be here" would be pure noise.
  - Reconstructed candidates lack `isbn13` (never stored on `book_candidates`),
    so the evidence-hash fidelity test covers the candidate-free path only;
    the free pass doesn't depend on hash matching (it reads `ai_decisions` by
    file id, not by hash).
  - A run is ~1 provider call per organised book (2 when it has an ISBN),
    bounded at concurrency 4 — minutes on the ~2200-book library. A provider
    429 is treated as "no data / inconclusive", never as "everything diverged".

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
