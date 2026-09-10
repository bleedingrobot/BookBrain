# Work prompts

Each file is meant to be run as its **own fresh Claude Code session** (start a
new chat, paste the file's contents or say "follow `prompts/NN-*.md`").

## Recurring

- [`review.md`](review.md) — a full read-only project review. Produces a written
  assessment and a fresh batch of numbered work-prompts. Run it every few weeks
  or after a burst of feature work.

## Standalone

- [`25-hardcover-integration.md`](25-hardcover-integration.md) — Hardcover API.
  **Phases 1 + 2 + 3 shipped 2026-09-08** (metadata provider; canonical series
  membership → named "missing books"; "Readers also liked" → wishlist).
  Phase 1.5 scoped.
- [`26-hardcover-phase-4.md`](26-hardcover-phase-4.md) — Hardcover Phase 4:
  metadata badges/filters (B), new & upcoming per series (A), Hardcover as a
  description source (C), characters (D). Order B → A → C → D.
  **Parts B + A + C shipped 2026-09-08** — B: curated
  rating/pages/category/genres → `hardcover_json.meta` → index v4 → viewer
  badges + genre facet + rating sort. A: series `release_date` → per-entry
  `releaseDate` → viewer "Next in …" / "Coming: … — <month year>" + a "Coming
  soon" filter chip. C: `hardcover_json.meta.description` is tried first (zero
  cost) by fill-missing-descriptions. **Part D (characters) skipped by design
  — `book_characters` data is too spotty; see prompts/26 header.**
- [`27-new-and-upcoming-releases.md`](27-new-and-upcoming-releases.md) —
  `RecentMarquee`-style scrolling cover strips for new / upcoming releases,
  filtered to authors + series the library knows, each cover → wishlist
  Request. Part 1 (series, ~free — Part A data is already in the index) → Part
  2 (per-author Hardcover pass + sidecar) → Part 3 (optional global "most
  anticipated"). **All three shipped 2026-09-08.** Part 1: per-entry `isbn13`
  in the series catalogue (index → v5), `collectSeriesReleases` + a
  generalised `<Marquee>` + `<ReleaseMarquee>` strips + `<ReleaseCard>` →
  Request. Part 2: `authors.hardcover_json` + `hardcover_new_releases_service`
  + `bookbrain-new-releases.json` + `<NewReleasesScreen>`; the strips show a
  merged series+author feed. Part 3: `fetch_global_anticipated` → sidecar
  `global` array → a third strip gated on a `showGlobalReleases` setting
  (default off). Not browser-verified; live sidecars need a refresh + index
  regen (or the nightly). **Backfilled + live 2026-09-08.**
- [`28-hardcover-author-identity.md`](28-hardcover-author-identity.md) — use
  Hardcover's `authors` graph (`canonical_id` / `alias_id` / `alternate_names`)
  to merge forked author rows the current `normalize_person_name` + shared-book
  heuristic can't (`Iain M. Banks`/`Iain Banks` with no shared book; pen name
  ↔ legal name). Phase 1 = resolve `authors.hardcover_person_id` (rides the
  prompts/27 per-author pass) + a merge pass in `repair_forked_authors.py`.
  Phase 2 (optional) = resolve at scan time so new books don't fork.
  **Both phases shipped 2026-09-09** — Phase 1: `resolve_person_id`
  canonical→alias walk verified live; repair pass 2 merges same-key rows
  sharing a person id and SUGGESTs pen-name pairs. Phase 2:
  `HardcoverProvider.resolve_person_id` + `scan_service` resolves it pre-lock
  and `_find_or_create_author` reuses a row by `hardcover_person_id` when the
  name-key misses (pen names, `George R. R. Martin` vs `George Martin`).
- [`29-semantic-search.md`](29-semantic-search.md) — natural-language
  ("meaning") search in the library-viewer: backend pre-embeds every book
  (all-MiniLM-L6-v2, ONNX, no torch) → `bookbrain-embeddings.bin` sidecar;
  viewer loads the same model once (vendored, ~23 MB, SW-cached) and embeds
  only the query, ranks by cosine. **All phases done + browser-verified
  2026-09-09.** Phase 0:
  free description backfill (882 blurbs, effective coverage 91% — later
  pushed to 96% via a working backend Google Books key + `refresh_epub`).
  Phase 1:
  `embedding_service` (all-MiniLM-L6-v2 ONNX, no torch) + `books.embedding`
  migration + `bookbrain-embeddings.bin` sidecar (int8) + nightly step +
  `POST /library/embeddings[/refresh]` + parity script. **Phase 2 done
  2026-09-09** — `@huggingface/transformers` (lazy chunk), model vendored in
  `public/models/`, `lib/embeddings.ts` + `lib/semanticSearch.ts` +
  Keyword|Meaning toggle in `App.tsx`, "· NN% match" per row. Parity
  cosine 0.99. **Browser-verified 2026-09-09** ("female assassin" → good
  results). Feature done + live.
- [`30-reading-status.md`](30-reading-status.md) — pull James's Hardcover
  reading data into a `bookbrain-reading.json` sidecar → Read/Unread/Want
  filters + rating badges in the viewer. Phase 2 unlocks "read next in a
  series you own" + an author-frequency signal for the release strips.
  Licence: James's own data / own token / own tool. **Phase 1 shipped
  2026-09-09** — `hardcover_reading_service`
  (paged `me{user_books}`) + `build_reading_payload` (ISBN then title match) +
  nightly + `POST /library/reading`; viewer `lib/reading.ts` + `ReadingBadge`
  + Read/Unread/Want chips. **Live: 385 matched** (231 read / 120 want / 29
  reading), 180 read books not in the library. **Phase 2 shipped 2026-09-09**
  — `nextInSeries` + `<ReadNext>` strip (~56 candidates, most-recently-active
  first), `readingProfile` weights the release strips by how much you read
  the author, `★ Favourites` filter (rating ≥ 4). **Want-to-read ↔ wishlist
  shipped 2026-09-09** — `wantUnowned[]` in the v2 reading sidecar → a "From
  your Hardcover want-to-read" card on the Wishlist screen (106 candidates
  live, one-tap Request). **Phase 3 (write-back) shipped 2026-09-09** — James asked for it
  ("both ways"). Viewer queues status changes (expanded-row buttons + auto
  "read" on finishing an epub) to `bookbrain-reading-pending.json`; backend
  `apply_pending` (ISBN/search resolve → `insert`/`update_user_book`,
  idempotent) flushes it on each `regenerate_reading`. Status only, never
  reviews.
- [`31-hardcover-more.md`](31-hardcover-more.md) — ten more things to leverage
  from Hardcover, nine parts, one commit each, value ÷ effort order: **A**
  moods / pace / content-warning tags (extends `meta`, index v6) · **B**
  rating-aware recs + want-to-read "quick wins" triage (viewer-only, uses the
  synced ratings) · **C** reading-goal progress line + a reading-stats screen
  (reading sidecar → v3) · **D** followed-authors signal for the release
  strips · **E** community lists ("on N lists" + list-sourced wishlist
  candidates) · **F** Hardcover Prompts → a "your library answers" screen ·
  **G** a gated "trending on Hardcover" strip · **H** edition metadata
  gap-fill (pages / pub year / audio hours) · **I** reading-progress two-way
  sync (resurrects `prompts/17` §E, Hardcover-backed — the big one, do last).
  A–H are catalogue data (shared-viewer safe) except B/C/D which use James's
  own synced data; I is the licence-sensitive one. Every query needs live
  verification first — field names in the doc are unverified. **Parts A, B,
  C, E, F, G, H, I shipped 2026-09-10; D dropped (no data)** — A: mood/content-
  warning tags; B: author-affinity rec re-rank + want-to-read quick-wins;
  C: `me{goals}` → goal bar + `<ReadingStatsScreen>`; E1: "on N lists" + a
  "Most listed" sort; E2: curated Hardcover lists → a "From lists you'd like"
  card on the Wishlist screen (`bookbrain-lists.json`); F: Hardcover Prompts
  → `bookbrain-prompts.json` + a "Your library answers" screen (+ a shared
  `match_hardcover_book_ids` helper); G: opt-in "Trending on Hardcover" strip
  (`new-releases` sidecar v2); H: edition pages/pub-year/audio-hours gap-fill;
  I: reading-progress two-way sync (`user_book_reads` — advance-only writes on
  reader close + "Reading · 62%" from Hardcover; reading sidecar v4). Index →
  v7. **D skipped** — Hardcover exposes no followed-authors to a PAT.
  **prompts/31 done bar the epub resume-at-position deferral + browser
  verification.**
- [`32-sff-news-feeds.md`](32-sff-news-feeds.md) — an SFF-news section under
  the release marquees: the backend fetches ~9 curated RSS/Atom feeds
  (Reactor, Locus, File 770, Grimdark Mag, Book Riot SF/F…), writes
  `bookbrain-news.json`, the viewer shows the latest merged headlines +
  excerpt + link-out. Same static-site/CORS → backend-sidecar pattern as the
  release strips. Adds `feedparser`. **Both parts shipped 2026-09-10** —
  `sff_news_service` fetches 9 feeds → `bookbrain-news.json` (nightly + `POST
  /api/library/news`); viewer `<NewsFeed>` compact section + `<NewsScreen>`
  with a source filter, `showNews` setting (default on). Live: 50 items.
- [`37-openbooks-acquire.md`](37-openbooks-acquire.md) — experimental "Find a
  Book" admin page backed by a locally-run OpenBooks server (IRC). Search →
  pick → backend downloads → uploads into the Drive inbox → normal pipeline.
  **Shipped 2026-09-10** — `openbooks_service` (single lock-serialised WS
  client), `acquire_service` (validate + upload + clean up), `GET/POST
  /api/acquire/{status,search,download}`, `frontend` "Find a Book" page,
  `backend/tools/run-openbooks.ps1` + a Start/Stop button on the page that
  launches it (`openbooks_process_service`). Off unless
  `OPENBOOKS_ENABLED=true`. Admin-only, never in the family viewer.
  Browser-verified (screenshot): server strip + search + results table. Inbox
  upload path is unit-tested (needs Drive creds to run live).

## 2026-09-10 review batch (`REVIEW-2026-09-10.md`) — open

Reviewed `prompts/29`–`32` + this session's fixes. Recommended order:
**33 → 36 → 34 → 35** (33 is the only P1; 33/36 are independent and fit one
session; 34/35 are independent of everything).

| # | File | Sev | Status | One line |
|---|------|-----|--------|----------|
| 33 | [`33-harden-reading-writeback.md`](33-harden-reading-writeback.md) | **P1**+P2 | **shipped 2026-09-10** | F1: `_resolve_book_id` now runs a title+author `_confident_match` before accepting a search hit; **and** the reader no longer auto-marks a book read on finish (James's call — deliberate ✓ button only). F2: failed write-backs dropped after 5 attempts. F4: 3 partial-and-smaller pulls → write `partial: true` + viewer note. F7: unmatched read-status rows that look owned are logged. |
| 34 | [`34-sidecar-sync-helper.md`](34-sidecar-sync-helper.md) | P2 | open | F6: 11 hand-rolled sidecar fetch modules (3 also hand-rolling cache+sync+serialised-write) → one `syncedSidecar<T>` primitive. F3: `bookbrain-reading-pending.json` can silently lose a "mark read" — no `If-Match` on the Drive PATCH; build ETag/412 retry into the helper. |
| 35 | [`35-admin-discovery-refresh.md`](35-admin-discovery-refresh.md) | P2 | open | F5: the admin app has no button to refresh any Hardcover/discovery sidecar — this session needed manual `curl` re-runs repeatedly. A "Discovery data" panel in Settings + `GET /api/library/discovery-status`. |
| 36 | [`36-feedparser-parse-timeout.md`](36-feedparser-parse-timeout.md) | P2 | open | F8: `feedparser.parse` runs with no timeout; a hijacked/garbage feed wedges the nightly news step. Wrap in `asyncio.wait_for`. |

## 2026-09-08 review batch (`REVIEW-2026-09-08.md`) — shipped

No P0/P1. Recommended order: 19 → 21 → 23 → 22 → 20 → 24. **All shipped 2026-09-08.**

| # | File | Sev | Status | One line |
|---|------|-----|--------|----------|
| 19 | [`19-gitignore-restore-artifacts.md`](19-gitignore-restore-artifacts.md) | P2 | **done** | `.gitignore` the `*.db.pre-restore-*` / `*.db.before-restore` / `backup-runs.log*` a restore leaves behind. |
| 20 | [`20-coauthor-identity-policy.md`](20-coauthor-identity-policy.md) | P2 | **done** | Policy 1 (file under primary author) — `_find_or_create_author` deterministic, harness scores on the primary, `repair_forked_authors.py` reworked (25 merges proposed on the real DB; James runs `--write`). |
| 21 | [`21-rule-match-title-trust.md`](21-rule-match-title-trust.md) | P2 | **done** | `find_rule_match` no longer auto-organizes an unverified/placeholder title at confidence 100. |
| 22 | [`22-librarysync-selective-rebuild.md`](22-librarysync-selective-rebuild.md) | P2 | **done** | `librarySync.ts` does a full tree rebuild on *any* sync error; only a stale sync token warrants that. |
| 23 | [`23-cover-service-tests.md`](23-cover-service-tests.md) | P3 | **done** | Finding was overstated — `cover_service` was mostly tested; added the missing `regenerate_covers` edge-path cases. |
| 24 | [`24-book-repository-match-cache.md`](24-book-repository-match-cache.md) | P2 | **done** | `book_repository.MatchCache` + `build_match_cache()` primed once per scan/rebuild batch in `_process_batch`, threaded to `resolve_book(match_cache=)`; `_find_or_create_author`/`_series` hit it (O(1) `session.get`) instead of the full-table scan. Miss falls back to the scan; one-off callers pass `None`. No behaviour change, corpus unchanged. |

## 2026-09-06 review batch (all shipped)

Ran in this order — 3 and 5 leaned on 1 and 2, but none was a hard dependency:

| # | File | One line |
|---|------|----------|
| 1 | [`01-ship-series-merge.md`](01-ship-series-merge.md) | Review, test and commit the uncommitted `series-merge` / library-audit work |
| 2 | [`02-scheduled-runs.md`](02-scheduled-runs.md) | Nightly unattended scan → organize → covers → index |
| 3 | [`03-epub-metadata-writeback.md`](03-epub-metadata-writeback.md) | Write the resolved title/author/series + cover into the EPUB itself |
| 5 | [`05-bulk-reidentify-audit.md`](05-bulk-reidentify-audit.md) | Re-check every organised book's identification, report what changed |

## 2026-09-06 review batch #2 (`REVIEW-2026-09-06.md`) — all shipped

Worked in one session via `prompts/12` (order 06 → 08 → 07 → 10 → 09 → 11),
commit per stage. SHAs + details in `REVIEW-2026-09-06.md` §"Shipped 2026-09-06".

| # | File | Sev | One line |
|---|------|-----|----------|
| 06 | [`06-title-collision-false-duplicates.md`](06-title-collision-false-duplicates.md) | P0 | `normalize_title` merges distinct books → `same_book` false positives → bulk trash |
| 07 | [`07-librarysync-missing-parents.md`](07-librarysync-missing-parents.md) | P1 | Viewer sync drops a cached file when a Drive change record omits `parents` |
| 08 | [`08-alembic-enum-drift.md`](08-alembic-enum-drift.md) | P2 | Migration for the `status`/`status_reason` enum additions; wire `alembic check` in |
| 09 | [`09-organize-write-lock.md`](09-organize-write-lock.md) | P2 | `OrganizeService._write_lock` not reset per test; unify the write locks |
| 10 | [`10-series-merge-undo.md`](10-series-merge-undo.md) | P2 | Series-merge Operations logged as undoable but Undo leaves a broken state |
| 11 | [`11-ai-spend-guardrails.md`](11-ai-spend-guardrails.md) | P2 | Cap + cost estimate for `descriptions?ai=true` and rebuild |

`REVIEW-2026-09-06-FIXPLAN.md` (repo root) resolves the open design choices in
06–11. [`12-work-the-review-fixes.md`](12-work-the-review-fixes.md) is the
staged, commit-per-stage prompt that works through all six in one session
(order: 06 → 08 → 07 → 10 → 09 → 11).

## Trustworthy identification (`prompts/13` + `14`) — all shipped

| # | File | Kind | One line |
|---|------|------|----------|
| 13 | [`13-trustworthy-identification.md`](13-trustworthy-identification.md) | hardening | **A + B** (`351850e`): `series_number` clamp + `UNCORROBORATED_SERIES_PENALTY` (structural gap #1). C + D split to prompt 14 |
| 14 | [`14-identification-learning-and-cover-dedup.md`](14-identification-learning-and-cover-dedup.md) | hardening | **C** (`c309245`): recent `/correct` pairs fed into the identify prompt as few-shot (+~209 tok worst case). **D** (`edff935`): `files.cover_phash` + "Near-identical cover art" panel in Library Audit |

## First-pass identification accuracy push (`prompts/15`) — COMPLETE 2026-09-07

All stages shipped: 0 (harness) + A–D (Tier 1) + E–H (Tier 2) + I/J/K (Tier 3).
See `15-identification-accuracy-push.md` Sequencing block, `IDENTIFICATION-EVAL.md`,
and `SPEC.md` § "Identification pipeline (2026)".

| # | File | Kind | One line |
|---|------|------|----------|
| 15 | [`15-identification-accuracy-push.md`](15-identification-accuracy-push.md) | umbrella / multi-session | Get first-scan identify+name+file accuracy toward ~100%. **Stage 0 landed & redesigned autonomous** (James wanted zero manual verification): 74-book corpus with **triangulated** answer keys (`scripts/build_truth.py` — Wikidata + 2 web-grounded Claude calls; a field counts only when ≥2 independent sources agree), `pytest -m corpus` gate, plus `test_identification_invariants.py` + `test_identification_mutation.py` (need no ground truth). Baseline is **partial** — API credit ran out mid-`build_truth`; re-run to complete. **Stage A shipped (2026-09-06)**: web-search grounding on the AI identify turn (`identify(prompt, ground=)` + `web_search_20260209` + `should_ground()` gate + `settings.ai_web_search_enabled`); offline corpus unchanged by construction, live measurement pending credit. **Stage B shipped (2026-09-06)**: Google Books + Open Library now populate `MetadataCandidate.series` / `series_number` / `genre` (F1); `SERIES_DISAGREEMENT_PENALTY` needs a provider consensus now. **Stage C shipped (2026-09-06)**: `providers/filename/parser.py` structured inbound-filename parse → labelled prompt block + `filename_corroborates` verdict replacing the weak substring test (F2); corpus `wrong_auto_organized` 2→1. **Stage D shipped (2026-09-06)**: spine-walking text snippet (`[front matter]` + `[body sample]`, skips cover/nav) + `EpubEvidence.publisher`/`pub_date`/`subjects`/`all_isbns` + `description` and all four into `_build_prompt`; `hash_evidence` untouched so the cached AI decisions stay valid. **Tier 1 complete.** **Tier 2 complete (2026-09-06)**: E placeholder/junk-metadata detector (fast-path skip + `PLACEHOLDER_METADATA_PENALTY`), F ISBN-trust check (`title_similarity ≥ 0.80` on the fast path), G positive confidence components (`DESCRIPTION_CORROBORATES` / `PUBYEAR_PLAUSIBLE`, additive) + `resolved_series` threaded through reident recompute, H verification pass (one adversarial `audit_book_identity` call for the 70–95 band, `settings.ai_verify_enabled` **off by default**). All offline-flat (frozen AI); E/F/G no AI cost, H opt-in. **Tier 3**: **J + K shipped (2026-09-07)** — J: `normalize_person_name` author match key + `Author.sort_name` + article-insensitive series match + `SeriesAlias` consulted/written on merge + dry-run repair/backfill scripts. K: `batch_prior_service` — a ≥3-file author/series consensus in a scan lifts a `review` file whose filename names it (+12, cap 92), before the auto-organize pass. Both corpus-flat (harness starts empty / scores one file at a time). Still to do: **I** (recently-auto-organized Dashboard tray + `settings.organize_hold_hours` soft-hold) — split into its own prompt below. One commit/stage. |
| 16 | [`16-stage-i-recently-organized-tray.md`](16-stage-i-recently-organized-tray.md) | prompts/15 Stage I | **DONE 2026-09-07.** `GET /api/library/recently-organized` + `recently_organized_service` + `RecentlyOrganized.tsx` Dashboard tray (Confirm/Correct/Confirm-all, 24h/48h/7d toggle); `POST /api/files/{id}/confirm` + `/files/confirm-batch` = idempotent `Review(approved)` signal; `settings.organize_hold_hours` soft-hold (default 0 = byte-identical no-op, one `discovered_at` WHERE clause) folded into `/settings/organize`. `Operation.confidence`/`model` now populated by organize. No AI cost. |

## Recent

| # | File | Kind | One line |
|---|------|------|----------|
| 18 | [`18-nightly-db-backup.md`](18-nightly-db-backup.md) | feature — backend + Settings | **DONE 2026-09-07.** `backup_service.py` — `VACUUM INTO` snapshot + `.sql.gz` dump → `backups/` subfolder of the Drive library folder on each nightly run (first, best-effort), last 7 kept. `POST/GET /api/library/backup[s]` + a Settings block. `RESTORE.md`. 572 backend tests green. |
| 17 | [`17-library-viewer-epub-reader.md`](17-library-viewer-epub-reader.md) | feature — `library-viewer` only | **§A–C DONE 2026-09-07** (`e08b700` + `511d2cf`). Vendored `foliate-js` (EPUB path, no npm dep) → `components/Reader.tsx`: full-screen paginated reader, tap/key/swipe, Contents drawer, Display panel (`readerPrefs.ts`), position in `localStorage` (`readingProgress.ts`), IndexedDB offline byte-cache (`bookCache.ts`, LRU 300 MB/20). "Read" on `.epub` rows + "Continue reading" strip + "Clear downloaded books". Build/lint/90 tests green; James-verified on real devices (`2ad7e40` + `c9c4c6b` follow-up fixes). **Considered complete.** §D (word-count→time-left), §E (cross-device position sync), Kobo sync — all dropped (James); position stays per-device, progress shown as `%`. |

## Shared context (every session should know this)

- **Repo:** `C:\Users\Giant\Documents\epub-librarian` — the directory keeps the
  old name; the project is **BookBrain**. Read `README.md`, `SPEC.md`,
  `ROADMAP.md`, and `AGENTS.md`/`CLAUDE.md` if present before starting.
- **Three apps:**
  - `backend/` — FastAPI + SQLAlchemy 2.0 async + Alembic, SQLite. Strict layering
    `api/ → services/ → providers/ → data/`. `cd backend && pytest` (includes
    `tests/test_migrations.py`, which runs `alembic upgrade head` + `alembic check`
    in a subprocess — schema drift fails the suite). Dev server
    `uvicorn app.main:app --reload` on `:8000`.
  - `frontend/` — React + TS + Vite + TanStack Query + Tailwind v4, the local
    admin UI. `cd frontend && npm run dev` on `:5173`, proxies `/api` to `:8000`.
    `npm run build` to typecheck.
  - `library-viewer/` — separate static React app, the family-facing browser.
    Deployed to GitHub Pages by a **GitHub Actions workflow on push to `main`**.
    `cd library-viewer && npm run build && npx vitest run && npm run lint`.
- **Deploy:** only `library-viewer` deploys (on push to main). `backend`/`frontend`
  run locally — nothing to deploy, but still build + test before committing.
- **Git:** work happens directly on `main`. Commit + push proactively when the
  task is green (build + tests pass). End commit messages with:
  `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>`
- **The user (James) is not terminal-savvy** — run commands yourself, don't hand
  him a list to type. He tests against a running app, not by reading diffs.
- **Windows gotchas that have bitten before:**
  - Orphaned `uvicorn --reload` workers hold the SQLite file → "database is
    locked". Check for and kill stragglers before blaming code.
  - A long `BackgroundTask` under `uvicorn --reload` can wedge the dev server.
  - Services serialize their SQLite writes with a **module-level `asyncio.Lock`**;
    `conftest.py` resets those per-test because `pytest-asyncio` gives each test
    its own event loop and a lock binds to the loop of its first acquire. If you
    add a new singleton lock, add a reset for it in `conftest.py`.
- **AI:** `anthropic_model = claude-opus-5`. Structured output via forced tool
  schema. Known recurring failure: the model reasons correctly that a `series`
  value is bogus, then emits it anyway — see the memory note
  "BookBrain AI series hallucination".
- **App-computed confidence is authoritative** (SPEC §1) — never route on the
  AI's self-reported confidence.
