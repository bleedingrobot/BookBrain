# Roadmap

Loose backlog — not commitments, just the ideas worth not forgetting.

## Later / maybe

- **Four review follow-ups (2026-09-06)** — briefs in `prompts/`, one per
  session: (1) ship series-merge *(done, see below)*, (2) scheduled nightly
  runs *(done, see below)*, (3) write resolved metadata + cover into the EPUB
  *(done, see below)*, (5) bulk re-identify audit *(done, see below)*. All four
  shipped.
- **Trustworthy identification (`prompts/13`)** — A + B shipped: a
  `series_number` sanity clamp (>50 / ≤0 → null, series name kept, original
  recorded in `raw_response.series_number_clamped`) applied on every
  identification return path, and a new `UNCORROBORATED_SERIES_PENALTY` (-15)
  in `confidence_service.score()` that fires when the resolved series appears
  in neither the EPUB nor any provider candidate — closing structural gap #1
  (invented series auto-organising silently).
- **Identification learning + cover dedup (`prompts/14`)** — both shipped.
  - **C**: `review_service.recent_corrections()` pulls recent human `/correct`
    pairs (author/series-relevant ones ranked first, no-ops dropped, cap 5),
    and `scan_service._process_file` feeds them into the AI identify prompt as
    few-shot "what a human fixed last time" examples — AI path only, fast path
    and `identify_series` untouched. Worst-case prompt-size delta measured at
    **+838 chars / ~209 tokens** for a full 5-example set (well under the
    ~400-token cap). `prompt_hash` now varies with correction history — fine,
    it was never a cache key.
  - **D**: new nullable `files.cover_phash` (16-hex pHash of the cover
    thumbnail, `imagehash` dep — pulls numpy+scipy), computed in
    `cover_service._make_one` and written back in bulk after the gather;
    `regenerate_covers` also backfills it from existing Drive `.jpg`s without
    re-downloading books. Library Audit → Split records gets a read-only
    "Near-identical cover art" panel listing different-identified file pairs
    whose covers are within Hamming ≤ 6 (`_COVER_HAMMING_THRESHOLD`). No
    auto-action — the human uses `/correct`. Follow-ups: a one-click merge
    button on the panel; a dismiss mechanism for publisher-template
    false-positives (tighten the threshold first if it's noisy on the real
    library).
- **First-pass identification accuracy push (`prompts/15`)** — umbrella
  multi-session plan to get first-scan identify/name/file accuracy toward
  ~100%. Deep-dive findings: providers never return a series (so the
  uncorroborated-series penalty is noise + a fast-path branch is dead code);
  the AI call is single-shot + ungrounded (post-cutoff books → invented
  series); `text_snippet` is usually just the cover page; `description` is
  parsed but never sent to the model; no filename parsing; no junk/placeholder
  metadata detection; the fast path trusts a possibly-wrong EPUB ISBN
  completely; `resolve_book` forks authors on "J.R.R." vs "J. R. R." and
  series on a leading article; `SeriesAlias` + `Author.sort_name` exist and
  are unused; nothing auto-organized ever gets a human glance. Stage 0 is a
  ground-truth eval harness (`pytest -m corpus` + `IDENTIFICATION-EVAL.md`) —
  every later stage must move the number. Then: web-search grounding, real
  provider series, filename→candidate parser, copyright-page text, placeholder
  detector, ISBN trust check, positive confidence components, a verification
  pass for the uncertain band, a "recently auto-organized" review tray +
  optional soft-hold, author canonicalisation, batch priors.
  - **Stage 0 shipped** (2026-09-06) — triangulated 74-book corpus,
    `pytest -m corpus` gate, `IDENTIFICATION-EVAL.md`.
  - **Stage A shipped** (2026-09-06) — web-search grounding on the AI identify
    turn: `AnthropicIdentificationClient.identify(prompt, ground=)` uses the
    `web_search_20260209` server tool to verify title/author/series/pub-year
    (told today's date), gated per-call by `identification_service.should_ground`
    — **recent-year signal only** (~3% of calls; a broader first cut grounded
    95% and tripled per-identify cost). Toggle `settings.ai_web_search_enabled`.
    Offline corpus unchanged by construction; live slice measurement still
    pending Anthropic credit.
  - **Stage B shipped** (2026-09-06) — real provider series (F1).
    `GoogleBooksProvider` + `OpenLibraryProvider` now populate
    `MetadataCandidate.series` / `series_number` / `genre`
    (Google: `seriesInfo.bookDisplayNumber` + numbered title parenthetical +
    `categories`; Open Library: search-doc `series`, `jscmd=data`
    `subjects` `series:`/`genre:` prefixes, one cached follow-up to the edition
    record). Shared `types.split_series_and_number`. `_build_prompt` prints
    `series=`/`genre=`/`published=` per candidate. `SERIES_DISAGREEMENT_PENALTY`
    now needs a **provider consensus** (≥2 agreeing candidates) so a lone messy
    provider string can't contradict a correct EPUB series. No AI-cost change;
    offline corpus flat by construction (fixtures predate provider series).
  - **Stage C shipped** (2026-09-06) — structured inbound-filename parser (F2).
    New `app/providers/filename/parser.py` → `parse_book_filename(name) ->
    FilenameGuess` (deterministic, no I/O; `Author - Title` /
    `Author - Series NN - Title` / `Title - Author` / `Last, First - Title` /
    `Title (Series NN)` / enclosed `(Year)` / lowercase names / site-tag strip;
    trailing Calibre `_1234` and absurd `(… #301)` never become a series
    number). `identify()` adds a labelled filename-parse block to the prompt and
    passes an explicit `filename_corroborates` verdict to `confidence_service`;
    `FILENAME_MATCHES_TITLE` is now that verdict, the old substring test kept as
    the fallback for `reident_audit_service` only. `scan_service` persists a
    `BookCandidate(source="filename")` for the audit (filtered out of the
    provider-consensus maths). Corpus `wrong_auto_organized` 2 → 1; per-field
    flat (frozen AI). No AI cost.
  - **Stage D shipped** (2026-09-06) — richer EPUB evidence (F2). Completes
    Tier 1. `_extract_text_snippet` walks the spine (skips cover/nav/titlepage +
    < 200-char docs; `[front matter]` = first 2 substantive docs, `[body sample]`
    = one doc ~20% in; 4000-char cap; old fallback for tiny books). New
    `EpubEvidence.publisher` / `pub_date` / `subjects` / `all_isbns` (every ISBN
    across all `<dc:identifier>` + `<dc:source>`). `description` + all four into
    `_build_prompt`. **`hash_evidence` deliberately unchanged** so the ~2200
    cached `ai_decisions` stay valid — richer evidence reaches only new files.
    Round-trip + fidelity test updated. No AI cost.
  - **Tier 2 shipped** (2026-09-06) — E/F/G/H.
    - **E** placeholder/junk-metadata detector: `metadata_sanity.looks_like_placeholder_title`
      / `_author` ("Unknown"/"Calibre"/"book1"/bare-number/publisher-as-author;
      short titles need ISBN/provider corroboration). Fast path skipped on a
      placeholder EPUB; `PLACEHOLDER_METADATA_PENALTY` -30 +
      `TITLE_IS_FILENAME_ONLY_PENALTY` -10 on the *resolved* metadata (opt-in
      via `resolved_title`/`resolved_author`).
    - **F** ISBN-trust check: `text_match.title_similarity` (difflib, strict
      normaliser); the fast path needs ≥ 0.80 title agreement with the
      ISBN-matched candidate, else falls through to the AI path.
    - **G** positive confidence: `DESCRIPTION_CORROBORATES` +3 /
      `PUBYEAR_PLAUSIBLE` +2 (additive, opt-in). `resolved_series`/`_title`/
      `_author` now threaded through `_recompute_confidence` (resolves the item
      below). Thresholds kept 85/95 (sweep in IDENTIFICATION-EVAL.md).
    - **H** verification pass: one adversarial `audit_book_identity` call for
      the 70–95 band — agree → +10 (cap 94), disagree → take-correction + force
      review, uncertain → force review. `settings.ai_verify_enabled` **defaults
      OFF** (one extra ~$0.03 call per uncertain new book).
  - **Tier 3 — J shipped** (2026-09-07) — author/series canonicalisation (F5).
    `text_match.normalize_person_name` (author match key: initials joined,
    "Last, First" reordered, first-of-co-author-list, lone middle initial
    dropped) + `person_sort_name` → `Author.sort_name` now populated.
    `_find_or_create_author` matches on the key (display name kept verbatim —
    rewriting co-author credits regressed the corpus). `_find_or_create_series`
    ignores a leading article + consults `SeriesAlias`; `apply_series_merge`
    writes a `SeriesAlias` per merged-away name (closes the re-fork loop —
    resolves the item below). `scripts/backfill_author_sort_names.py` +
    `scripts/repair_forked_authors.py`, both dry-run default.
  - **Tier 3 — K shipped** (2026-09-07) — batch priors (F6).
    `app/services/batch_prior_service.apply_batch_priors`, called by `run_scan`
    between the batch and the auto-organize pass. A ≥3-file author/series
    consensus lifts a `review` file in the same scan whose filename names it
    (+12, cap 92, drops the pending Review if it clears 85); a disagreement is
    logged, not acted on. Never rewrites an identification; all changes under
    `ai_decisions.raw_response_json["batch_prior"]`. **Stage I** (recently-organized
    tray + `organize_hold_hours` soft-hold) is the last piece — frontend + API.
- **Reident recompute + uncorroborated-series penalty** — ~~`reident_audit_service._recompute_confidence` deliberately does *not* pass~~
  **RESOLVED by Stage G (2026-09-06)** — it now passes `resolved_series` /
  `resolved_title` / `resolved_author`, so the audit's display recompute matches
  what a fresh scan of the book scores today. (Original note kept below for
  history.)
  `reident_audit_service._recompute_confidence` deliberately does *not* pass
  `resolved_series` yet, so historical books aren't retroactively penalised.
  Deciding to apply it there (probably yes) is its own change + test.
- ~~**Series merge — auto-create a `library_rule` (series_alias)** on apply~~
  **DONE (2026-09-07, prompts/15 Stage J)** — `apply_series_merge` writes a
  `SeriesAlias` row per merged-away name and `_find_or_create_series` consults
  the table, so the next scan phrasing the old name resolves straight to the
  canonical Series row instead of re-forking it.
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

### Feature ideas from the 2026-09-06 "what would improve this?" think

Prompt `13-trustworthy-identification.md` covers the #1 theme (proactive
mis-identification guards). The rest of that session's ideas, for later:

- **Viewer as a reading tool, not just a shelf.** The family-facing app is
  "browse what we own"; the payoff features are about *reading*:
  - Reading state per person — want-to-read / reading / finished. Even manual
    toggles unlock everything below.
  - Next-up surfacing — flip `seriesGaps.ts` around: "you finished Mistborn #1,
    you own #2" on the home screen; "you own #3 but not #1–2" → offer to add to
    the wishlist.
  - Ratings + a one-line note per book, per reader.
  - **Natural-language search** — "that sci-fi one about a generation ship."
    Embed the descriptions locally (`sentence-transformers`, no API cost) and do
    semantic search. The real daily use case for a personal library is "I know
    we own it, I can't remember the title."
  - Kobo reading-stats round-trip — `KoboReader.sqlite` on the device has
    reading position + time-spent; pull it back on the nightly job for real
    "finished" detection and a year-end "reading wrapped".
- **Observability + safety net.**
  - **AI cost ledger** — batch-11 added *estimates*; there's no record of
    *actual* spend. Wrap `AnthropicIdentificationClient` to log every call with
    token counts + computed cost to a table; running month total on the
    Dashboard.
  - **DB backup to Drive on the nightly run** — `epub_librarian.db` is a single
    point of failure holding 2,200 books of resolved metadata + human
    corrections; the Sheets export is lossy. Copy the actual `.db` (or a full
    JSON dump) into a Drive `backups/` folder nightly, keep the last 7.
  - **Library-health panel** — mostly queries that already exist: N with no
    cover, N with no description, N stuck in review > 30 days, N series with
    gaps, N low-confidence auto-organised, folder-drift count. One screen for
    "is the library in good shape?".
- **Close the acquisition loop.**
  - Auto-match a file landing in the torrents watch folder against a `wanted`
    wishlist item → flag / auto-approve instead of cold identification.
  - New-release watch for the most-read authors (Google Books / OpenLibrary) →
    wishlist suggestions. Needs an author-frequency signal, which reading state
    provides.
- **Thin read-only backend for the viewer.** Today every family member who
  signs in grants full `drive` read/write to their whole Google account (the
  scary consent screen). A minimal proxy serving the sidecar JSON + covers via a
  service account removes that — and is the gate on the viewer ever being more
  than 2 users.

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
