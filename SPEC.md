# EPUB Librarian — v1 Design Spec

Auto-organizing Google Drive EPUB library manager. AI-assisted metadata identification, confidence-gated automation, human review for anything uncertain.

## 1. Resolved contradictions

**Drive "folders" are parent references, not paths — enforce single-parent.**
A file can have 0, 1, or >1 parents in Drive's model. The app requires exactly 1. During scan:
- `parents.length > 1` → `files.status = 'review'`, reason `multi_parent`, all parent folder names shown in the review UI. The app never auto-removes a parent.
- `parents.length == 0` (occurs on some Shared Drive edge cases) → same treatment, reason `no_parent`.
- Never assume `drive_parent_id` is authoritative without re-reading current parents on each scan.

**Manual drift detection.** If a file the app previously organized (present in `operations` with `status='done'`) now has a Drive parent that doesn't match what the app last set, don't silently re-move it. Mark `status='review'`, reason `manual_drift`. The user's out-of-band reorganization wins until they resolve it.

**Dry-run is Milestone 6a, not a vague aside.** Milestone 6 builds move/rename/logging but wires it only to a dry-run flag that defaults `true`. Milestone 6a is dry-run validation: the user runs a full scan+identify+dry-run-organize pass, reviews the log of *what would have happened*, and explicitly flips the flag. Milestone 7 (review queue) and Milestone 6 auto-move enablement both depend on 6a having been exercised at least once — this is a gate, not a suggestion.

**Correction stickiness, keyed by content not by file row.** Cache AI decisions by `sha256(file) + evidence_hash` as the default path. But corrections are stickier than cache and are keyed by `sha256` alone (not `file_id`), because the same EPUB content can appear under multiple `files` rows (duplicates, re-uploads). Pipeline order on every identification pass:
1. Check `reviews` for any `status='corrected'` row sharing this file's `sha256`. If found, use its `correction_json` directly — confidence 100, source `user_correction`, skip providers and AI entirely.
2. Else check the `ai_decisions` cache keyed by `sha256 + evidence_hash`.
3. Else run the full pipeline (§5).

A correction always invalidates the corresponding `ai_decisions` cache row for that hash.

**Conflict penalty is quantified, not qualitative.** The §13 point table gets explicit deductions, stacked with positive components and clamped to `[0, 100]`:

| Conflict | Penalty |
|---|---|
| Two or more external providers disagree on title or author | −25 |
| EPUB-embedded metadata disagrees with provider consensus | −15 |
| Series or series-number disagreement only (title+author agree) | −10 |

The specific disagreeing fields and sources are stored in `metadata_sources`/`book_candidates` and rendered in the review UI (§40) — a low score always comes with a visible reason.

**App-computed confidence is authoritative; AI self-reported confidence is advisory only — hard rule, not implied.** `ai_decisions.computed_confidence` (from the §13 table, including penalties above) is the only value automation thresholds read. `ai_decisions.ai_reported_confidence` is stored and shown in the UI as an explanatory signal but is structurally never passed to the threshold-routing function — enforce this by not even threading `ai_reported_confidence` into the routing code path's function signature, so it can't accidentally leak in.

**EPUB parsing is a hostile-input surface, not just a no-JS sandbox.** "EPUB executes nothing" (§36) covers script/JS. Malformed/adversarial zip and XML content is a separate attack surface, closed explicitly:
- Per-entry and cumulative decompressed-size caps enforced while streaming (reject if any single entry >100MB or cumulative >500MB), checked via `ZipInfo.file_size` before extraction — classic zip-bomb guard.
- Cap total zip entry count (e.g. reject >10,000 entries).
- Parse OPF/NCX/XHTML with `defusedxml`, never raw `lxml`/`ElementTree` — disables DTD and external entity resolution, closes XXE.
- Hard wall-clock timeout on the parse step (10s) so one bad file can't hang the scan job.
- Any failure under these limits → `files.status = 'unidentified'`, reason logged, scan job continues.

## 2. Additional gaps closed

**Shared Drives are out of scope for v1.** Only "My Drive" is supported. The folder picker rejects a Shared Drive selection at pick time with an explicit message, rather than silently failing later when `supportsAllDrives`/`includeItemsFromAllDrives` aren't set on API calls.

**Trashed files excluded explicitly.** Scanner queries filter `trashed=false` explicitly rather than relying on default list-call behavior.

**OAuth scope must be decided before consent, not after.** Whether the app requests `drive.file` (app-created folder) or `drive` (folder-restricted, user-created folder) changes the consent screen itself. First-run flow is therefore: ask "did you already create the inbox folder, or should the app create one?" → request the corresponding scope → OAuth consent → (if user-created) Drive Picker to select it → store folder ID in `settings`. Not: consent first, discover the scope mismatch during folder pick.

**`library_rules` — when it's read, not just written.** Rules are consulted during evidence assembly (§5 step 1), *before* provider lookups and before AI — a matching rule contributes a candidate directly with confidence 100 and source `library_rule`, short-circuiting the rest of the pipeline exactly like a sticky correction does. `rule_type` values for v1: `filename_pattern` (regex → book_id or author/series hint), `author_alias`, `series_alias`. Rules are created two ways: automatically when a user resolves a review and checks "apply to similar" (writes `created_from_review_id`), or manually via Settings. This is what makes corrections generalize instead of only fixing one file at a time.

## 3. Simplifications for v1 (unchanged from prior discussion)

- `BookMetadataProvider` interface built, but only Google Books implemented first, Open Library second. No plugin system.
- AI provider hard-coded to Anthropic's Messages API with structured JSON, behind one thin service class.
- No webhooks/near-real-time watching — manual scan only, designed as an idempotent function so a scheduler can call it later.
- No natural-language search / conversational librarian in v1 — schema (books/authors/series normalized) must not preclude it later.
- No EPUB metadata repair/writing — read-only parsing only.
- Single user, one OAuth-connected Google account, config in local settings table.

## 4. Architecture

Modular monolith. FastAPI backend, SQLite, FastAPI `BackgroundTasks` for v1 (not Celery/Redis — over-engineering per §48, interface designed so a real queue can slot in later). React + TypeScript + Vite frontend, separate dev server proxying to FastAPI.

Strict layering:
```
API layer (FastAPI routers)
   → Service layer (business logic, orchestration)
      → Provider layer (Drive, EPUB parser, metadata APIs, AI client)
         → Data layer (SQLAlchemy models / repositories)
```
Nothing in `api/` talks to `google_drive/` or `ai/` directly — always through a service. This is what makes mocking for tests (§35) tractable.

## 5. AI identification pipeline

1. Check `library_rules` for a match (filename pattern / author alias / series alias) → if matched, short-circuit with confidence 100, source `library_rule`.
2. Check for a sticky correction on this file's `sha256` (§1) → if found, short-circuit with confidence 100, source `user_correction`.
3. Assemble deterministic evidence: filename, EPUB metadata, ISBN, title/copyright page text — capped at ~2–3k tokens.
4. Generate candidates from metadata providers: ISBN lookup first, then title+author fallback.
5. If ISBN + provider + EPUB metadata all agree → skip AI, compute confidence deterministically from §13 table, done.
6. Otherwise call `BookIdentificationService`: evidence + candidates → Anthropic, forced JSON schema (`title`, `author`, `series`, `series_number`, `ai_confidence`, `reasoning_summary`, `needs_human_review`).
7. App recomputes confidence independently from the §13 table including conflict penalties (§1) — `ai_confidence` is stored but never drives routing.
8. Threshold routing on `computed_confidence`: ≥95 auto-organize, 85–94 auto + flagged in audit log, 70–84 review queue, <70 left alone/unidentified.
9. Cache the decision keyed by `sha256 + evidence_hash`; invalidated on manual correction (§1).

## 6. Database schema (v1)

```
authors(id, name, sort_name, created_at)
series(id, name, created_at)
series_aliases(id, series_id, alias)
books(id, canonical_title, author_id, series_id, series_number,
      description, language, first_published, created_at)
identifiers(id, book_id, type[isbn10|isbn13|other], value, source)
files(id, drive_file_id, drive_parent_id, filename, sha256, size_bytes,
      book_id NULL, status[inbox|organised|review|unidentified|duplicate],
      status_reason NULL,  -- multi_parent | no_parent | manual_drift | parse_failed | ...
      quality_score, discovered_at, last_processed_at)
metadata_sources(id, file_id, field_name, value, source, retrieved_at)
book_candidates(id, file_id, title, author, series, series_number,
                 source, confidence_component_json)
ai_decisions(id, file_id, model, prompt_hash, evidence_hash, raw_response_json,
             computed_confidence, ai_reported_confidence,
             needs_human_review, reasoning_summary, created_at)
operations(id, timestamp, file_id, action, original_name, original_parent_id,
           new_name, new_parent_id, confidence, model, reason, status[done|undone])
reviews(id, file_id, status[pending|approved|rejected|corrected],
        proposed_json, correction_json, resolved_at)
library_rules(id, rule_type[filename_pattern|author_alias|series_alias],
              pattern, resolution_json, created_from_review_id)
settings(key, value)
```
Foreign keys enforced. `files.sha256` indexed for exact-duplicate lookup and correction stickiness. `identifiers.value` indexed for ISBN lookup. `ai_decisions` indexed on `(file_id, evidence_hash)`.

## 7. API structure

Per §28, plus: `POST /api/scan` returns a job ID immediately (`{job_id, status: "running"}`), frontend polls `GET /api/scan/{job_id}` — scanning hundreds of files shouldn't block a request/response cycle.

## 8. Frontend structure

```
src/
  pages/        Dashboard, Inbox, ReviewQueue, Library, Duplicates, Activity, Settings
  components/   BookCard, ConfidenceBar, EvidenceList, ReviewDialog, DiffPreview
  hooks/        useScanStatus, useReviewQueue, useActivity
  services/     api client (typed fetch wrappers per endpoint)
  types/        shared TS interfaces mirroring Pydantic schemas
```
Review dialog is the most important component: evidence, confidence breakdown (including any conflict penalties applied and why), and proposed action, per the §42 UI principle. Approve / Edit / Reject actions; Edit writes `reviews.correction_json` and triggers the sticky-correction path for future scans of that content.

## 9. Technology stack

| Layer | Choice | Why |
|---|---|---|
| Backend framework | FastAPI + Pydantic v2 | async, structured schemas, matches §28 |
| DB | SQLite + SQLAlchemy 2.0 (async) + Alembic | migration path to Postgres per §27 |
| EPUB parsing | ebooklib + manual OPF/NCX inspection, defusedxml for all XML | most maintained pure-Python EPUB lib; XXE-safe |
| ISBN validation | isbnlib | checksum + normalization built-in |
| Drive API | google-api-python-client + google-auth-oauthlib | official SDK |
| AI | Anthropic Messages API, structured JSON via forced schema | avoids prose parsing |
| Background jobs | FastAPI BackgroundTasks for v1 | avoids infra overkill now, interface allows Celery/RQ later |
| Frontend | React + TypeScript + Vite + TanStack Query + Tailwind | matches §29 |
| Testing | pytest + pytest-asyncio + responses/respx | matches §35 |

## 10. Google OAuth

Standard Google Cloud OAuth 2.0 flow. Scope decided *before* consent per §2 above. Tokens stored encrypted at rest locally, refresh token for long-lived access. First-run flow: ask folder-creation question → request scope → OAuth consent → (if applicable) Drive Picker → store folder ID in `settings`. Shared Drives rejected at pick time.

## 11. Development milestones

1. Project scaffold, DB schema, empty API/frontend shells.
2. OAuth + Drive listing (My Drive only, Shared Drives rejected at pick time).
3. EPUB parsing (with safe-parsing constraints from §1) + evidence extraction.
4. Metadata providers: Google Books, then Open Library.
5. AI identification pipeline (§5) + confidence scoring (§13 + conflict penalties).
6. Move/rename/logging, wired only to a dry-run flag (default `true`).
   **6a. Dry-run validation gate** — user runs full scan+identify+dry-run pass, reviews the log, explicitly flips the flag. Required before 7 and before any auto-move is enabled.
7. Review queue UI (Approve/Edit/Reject), correction stickiness (§1) and `library_rules` generation from reviews.
8. Threshold-gated automation enabled (≥95 auto, 85–94 auto+flagged), duplicates/quality scoring.
9. Activity log, undo (via `operations.status`), settings polish.
