# EPUB Librarian

Auto-organizing Google Drive EPUB library manager. See [SPEC.md](SPEC.md) for the full design.

## Layout

- `backend/` — FastAPI + SQLAlchemy + Alembic (async), layered `api/ → services/ → providers/ → data/`
- `frontend/` — React + TypeScript + Vite + TanStack Query + Tailwind v4 (local admin UI)
- `library-viewer/` — React + Vite + Tailwind, the family-facing browser. Reads the
  `bookbrain-index.json` sidecar + covers straight from Drive (no backend). Deployed
  to GitHub Pages on push to `main`. Includes an in-browser EPUB reader built on a
  **vendored** copy of [foliate-js](https://github.com/johnfactotum/foliate-js)
  (`src/vendor/foliate/`, EPUB path only, no npm dependency — see its `VERSION`).
  Reader typography/theme and per-book reading position live in `localStorage`;
  opened books are cached in IndexedDB for offline reading.

## Backend

Requires Python 3.11+.

```
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env   # fill in credentials, see below
alembic upgrade head
uvicorn app.main:app --reload
```

Runs at `http://localhost:8000`. Tests: `pytest`.

Dependencies (including APScheduler, added for the nightly run) are declared in
`pyproject.toml` — `pip install -e ".[dev]"` installs them. After pulling changes,
re-run that and `alembic upgrade head`.

### Nightly unattended run

Settings → **Nightly run** turns on a once-a-night pass that does the whole
pipeline with no one watching: pull the Torrents folder, scan the Book Dump,
auto-organize everything above the confidence threshold, then refresh covers and
the library index. It never resolves a review or clears a duplicate — uncertain
books still wait in the queue.

Two layers run the same job (`app/jobs/nightly.py::run_nightly`):

- **In-process** — an APScheduler job in the FastAPI lifespan. Fires only if the
  server is up at the chosen hour. Toggling the setting re-arms it live.
- **Standalone** — `python -m app.jobs.nightly`, no HTTP layer, exits non-zero on
  failure, logs to `backend/nightly-runs.log`. For when the machine's usually not
  running the server overnight: double-click `backend/scripts/register-nightly-task.bat`
  once to install a Windows Scheduled Task (2am by default; pass an hour to match
  the in-app setting). `unregister-nightly-task.bat` removes it.

A dead Google token makes either layer log "reconnect Google in Settings" and stop
cleanly. The Dashboard shows the last run's result.

### Google OAuth setup (needed for Milestone 2+)

1. In [Google Cloud Console](https://console.cloud.google.com/), create a project, enable the **Google Drive API**, and create an **OAuth client ID** of type "Web application".
2. Add `http://localhost:8000/api/auth/callback` as an authorized redirect URI.
3. Put the client ID/secret in `backend/.env` as `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`.
4. Generate `TOKEN_ENCRYPTION_KEY`: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.

While the app is in "Testing" publishing status in Google Cloud Console, only test users you add explicitly can complete the consent screen.

## Frontend

Requires Node 18+.

```
cd frontend
npm install
npm run dev
```

Runs at `http://localhost:5173`, proxies `/api` to `http://localhost:8000`.

## Status

- Milestone 1 (project scaffold, DB schema, empty API/frontend shells) — done.
- Milestone 2 (OAuth + Drive listing, My Drive only) — done. Encrypted token storage,
  scope-before-consent flow (§10), folder picker with Shared Drive rejection (§2),
  multi-parent/no-parent flagging on listing (§1).
- Milestone 3 (EPUB parsing + evidence extraction) — done. `/api/scan` now does real
  work: lists the inbox folder, skips files already known (idempotent rescans), downloads
  and SHA-256-hashes anything new, parses it with hand-rolled defusedxml-based OPF/NCX
  parsing (no ebooklib on the untrusted-file path), and stores evidence in
  `metadata_sources`. Safe-parsing limits from §1 are enforced and tested: per-entry and
  cumulative decompressed-size caps, entry-count cap, XXE/billion-laughs rejection, parse
  timeout. Files land in `inbox`, or `review` if multi-parent/no-parent, or
  `unidentified` if parsing fails.
- Milestone 4 (metadata providers) — done. `BookMetadataProvider` interface with
  `GoogleBooksProvider` and `OpenLibraryProvider` behind it (no plugin system, per SPEC).
  `CandidateService` does ISBN lookup first, aggregating across *all* providers (not just
  the first responder) so Milestone 5's conflict-penalty scoring (§13) has something to
  compare — falls back to title+author search only if ISBN lookup yields nothing from any
  provider. Wired into the scan pipeline: every newly-scanned file's evidence now also
  generates `book_candidates` rows. Live-tested against the real APIs, not just mocks:
  Open Library returned real data for a live ISBN lookup; Google Books hit a shared-sandbox
  quota limit (HTTP 429), which confirmed the graceful-degradation path (return `[]`, don't
  crash the scan) against a genuine failure, not a simulated one.

- Milestone 5 (AI identification pipeline) — done. `IDENTIFY_BOOK_TOOL` forces structured
  JSON via `tool_choice` + `strict: true`; `AnthropicIdentificationClient` is the one thin
  service class the model lives behind (SPEC's v1 simplification). Confidence is a
  from-scratch point table (§13 was never available to this project beyond "sums to 100,
  40+20+15+15+5+5" — this build defines the six components + the §1 conflict penalties
  concretely; see `app/services/confidence_service.py`) — **computed confidence, never the
  AI's self-reported one, drives routing**, enforced structurally: `ai_reported_confidence`
  is stored on `ai_decisions` for the review UI but the threshold check only ever reads
  `computed_confidence`. Deterministic fast path (ISBN + a provider + EPUB metadata all
  agree) skips the AI call entirely and — deliberately — tops out at 95, one point short of
  auto-organize eligibility, since it never earns the AI-corroboration point. Threshold
  routing: <70 → `unidentified`, 70-84 → `review` + `low_confidence`, ≥85 → stays `inbox`
  (auto-organize *execution* is Milestone 6, not this one). A structural issue
  (multi-parent/no-parent) always overrides confidence-based routing. `Book`/`Author`/
  `Series`/`Identifier` rows are found-or-created per identification.

- Milestone 6 (move/rename/logging, dry-run gated) — done. `OrganizeService.build_target_path`
  is a pure function (`{Author}/{Series}/{N - }{Title}.epub`, sanitized for illegal path
  chars) — testable without touching Drive. A separate, generic "library" folder role was
  added alongside the existing "inbox" role (same picker/create UI, mirrored settings keys)
  so source and destination are never accidentally the same folder. `/api/organize` reads
  `organize_dry_run` from `settings` — **defaults to `true` unless explicitly set to
  `"false"`**, satisfying SPEC's "dry-run until explicitly flipped" requirement even on a
  fresh, never-configured install. A dry run never calls Drive at all (no folder
  create/lookup) and logs the *intended* path as a display string on `operations.new_parent_id`
  rather than a real folder ID, since no folder was created to give it one — `operations.dry_run`
  (new column, migration `e4ef4e5285d7`) distinguishes a logged preview from a real move.
  Only a real run creates/reuses nested Author/Series folders, calls `files.update` to
  move+rename, and flips `files.status` to `organised`. The frontend's dry-run toggle
  requires a two-step confirm with an explicit warning before enabling live moves — Milestone
  6a's "review a dry run before flipping" intent, enforced by friction since the review queue
  UI itself doesn't exist until Milestone 7.

- Milestone 7 (review queue UI, correction stickiness, library_rules) — done. Scan now
  creates a `reviews` row (status `pending`) for every file that lands in `review` status —
  both confidence-based (`low_confidence`) and structural (`multi_parent`/`no_parent`).
  Approve/Edit/Reject are real endpoints: **Approve** promotes the file to `inbox` (organize
  eligible) unless the review reason is structural, in which case the Drive-side conflict is
  left untouched — approving confirms the identification is correct without pretending the
  folder conflict is resolved. **Reject** clears `file.book_id` and marks `unidentified`.
  **Correct** re-resolves (find-or-creates) a `Book` row from the user's values — carrying
  forward the *old* book's ISBN identifiers, since a title/author fix shouldn't lose a
  correct ISBN — and, if "apply to similar" is checked, diffs the correction against the
  original proposal to create an `author_alias`/`series_alias` `library_rules` row (only
  when that specific field actually changed, so an unrelated title fix doesn't spuriously
  generalize).

  Correction stickiness (§1) and library_rules matching are wired into the scan pipeline as
  short-circuits, checked *before* candidate generation (not just before the AI call — when
  either hits, `CandidateService.generate_candidates` is never invoked, verified by a test
  that makes the injected candidate service throw if called): `find_rule_match` checks
  `library_rules` for an `author_alias`/`series_alias` whose pattern matches the EPUB's own
  author/series string (confidence 100, `ai_decisions.model = "library_rule"`). Correction
  lookup by sha256 turned out to structurally belong to Milestone 8's duplicate detection
  instead of here — see that entry below for why, and for where the logic actually lives now
  (`resolve_corrected_book_id`).

  Scope note: `library_rules.rule_type = filename_pattern` exists in the schema but has no
  matching or generation logic yet — auto-generating a safe regex from one correction is a
  materially harder problem than a straight alias substitution, and was left out rather than
  shipped fragile. Also out of scope: the `ai_decisions` evidence-hash cache mentioned in
  earlier design notes (§25) — corrections and rules cover Milestone 7's actual ask; the
  cache is a cost optimization, not a correctness requirement, and every file still only
  gets identified once today (scan is already idempotent per file).

- Milestone 8 (duplicate detection, quality scoring) — done. Threshold-gated automation
  itself was already built (Milestone 6's dry-run/live organize + Milestone 5's confidence
  routing), so this milestone was specifically the two items SPEC bundles alongside it.

  **Duplicate detection** turned up a real design bug while building it, not just a feature:
  I'd built Milestone 7's `find_correction` keyed by sha256, called from the main
  identification pipeline. Milestone 8 needed a *second* sha256-keyed check — "does another
  `File` row already have this exact content" — and it has to run first, since a duplicate
  skips parsing entirely (identical bytes ⇒ identical evidence, no reason to re-parse or
  re-identify). But a correction can only exist for a sha256 that's already attached to some
  `File` — so once the duplicate check runs first, `find_correction` becomes dead code: by
  the time you'd reach it, the duplicate branch has already fired and returned. My first pass
  didn't catch this and shipped both checks in the same request, which a test caught
  immediately (a file matching a corrected sha256 was landing as `status=inbox` via the full
  identification path *and* should have been a `duplicate` — it could only ever be one).
  Fixed by removing `find_correction` and its now-redundant tests, and folding what it did
  into `resolve_corrected_book_id`: the duplicate branch now checks for a correction on that
  content and inherits the corrected book identity instead of blindly copying the (possibly
  never-reviewed, possibly wrong) primary copy's `book_id`. A file whose content has no
  match anywhere gets processed normally; a repeat gets `status=duplicate`, no
  `metadata_sources`/`book_candidates`/`ai_decisions` rows (nothing to derive — the primary
  already has them), and either the primary's `book_id` or, if that exact content was later
  corrected by a human, the corrected one.

  **Quality scoring** (`quality_service.score_quality`) is a from-scratch heuristic — SPEC
  never defined one, same situation as §13's confidence table in Milestone 5. It measures
  the *file's own* completeness (title/author/language/ISBN/text-snippet presence, plus a
  10KB-minimum sanity floor against stub/corrupted EPUBs) — deliberately not the same
  concept as identification confidence, which is about knowing *which* book it is, not
  whether the file itself is any good. It does not gate automation or status routing; it's
  informational, surfaced via `GET /api/duplicates` and the new Duplicates page. No delete or
  resolve action was built for duplicates — deciding what to do with a redundant Drive file
  is a destructive, user-facing decision this milestone's spec language ("detection") didn't
  ask for.

- Milestone 9 (activity log, undo, settings polish) — done, and the last of the nine
  milestones. Activity page lists every `operations` row (dry-run and real alike, dry runs
  clearly badged). **Undo** reverses a completed, real move: calls `files.update` back to
  `original_parent_id`/`original_name`, sets the file back to `inbox` (not "whatever it was
  before," since the only thing that could have led to a real organize op is a file that was
  `inbox` at the time — that's organize's own precondition), and flips the operation to
  `undone`. Rejected with 409 for a dry-run entry (nothing to undo) or an already-undone one
  — `OperationNotUndoableError`, not a silent no-op.

  Settings polish surfaced one more real gap while wiring the new "confidence thresholds"
  display: `confidence_auto_organize`/`confidence_auto_flagged`/`confidence_review_queue`
  existed in `config.py` since Milestone 1 but scan_service's actual routing used hardcoded
  `70`/`85` literals — the settings were never actually read. Fixed by wiring them in rather
  than shipping a display that shows numbers the backend doesn't act on. `confidence_auto_organize`
  (95) still isn't a distinct behavioral gate — v1 has two behavioral tiers (review-eligible
  vs organize-eligible), not three; the 85-94-vs-95+ "flagged" distinction SPEC's original
  language implies is satisfied by `operations.confidence` already being visible per-entry
  in Activity, not by a separate status. Settings also now shows whether
  `ANTHROPIC_API_KEY`/`GOOGLE_BOOKS_API_KEY` are configured (booleans only, never the
  values) via `GET /api/settings/status` — useful for telling "AI identification is failing"
  apart from "AI identification was never configured."

All nine milestones from SPEC.md §11 are now done. See SPEC.md for the original design and
the sections above for what changed or got resolved along the way.

Live OAuth roundtrip and live Drive scan (real move/rename/undo included) are still
untested against a real Google account — verified so far via unit tests and a
live-but-disconnected backend, plus one live full-stack smoke test per milestone (seed real
data into a real SQLite file, boot the real server, hit the real endpoints over HTTP — not
just the in-memory test suite) to catch integration issues the unit tests alone would miss.
The Anthropic identification client **has** been live-verified end-to-end against the real
API with a real `ANTHROPIC_API_KEY` (correctly identified "Dune" by Frank Herbert from
evidence alone, forced tool-use parsed cleanly) — see the Google OAuth setup section above
for the equivalent Drive walkthrough before testing Scan/Organize/Undo against a real Drive
account.
