# Task 16 — prompts/15 Stage I: "recently auto-organized" tray + optional soft-hold

Read `prompts/README.md` for shared context. This is the **last remaining stage**
of the `prompts/15` identification-accuracy push (Tiers 1 + 2 + Stages J/K are all
shipped — see `prompts/15-identification-accuracy-push.md` Sequencing block and
`IDENTIFICATION-EVAL.md`). It was split out because, unlike J and K, it is
frontend + API + a new setting and needs verifying against a running app.

## First read

- `prompts/15-identification-accuracy-push.md` — the whole plan; **§"Tier 3 →
  Stage I"** is the spec this task implements verbatim, plus the notes below.
- `SPEC.md` §5 (pipeline + threshold routing), §7 (API), §8 (frontend structure).
- Memories: `project-bookbrain-identification-accuracy-push` (PROGRESS block —
  everything shipped so far and why the corpus number never moved),
  `feedback-bookbrain-api-cost` (this stage adds **zero** AI cost — keep it that
  way), `project-bookbrain`.
- `IDENTIFICATION-EVAL.md` — append this stage's before/after like the others.

## Why (finding F6)

Everything `>= confidence_auto_flagged` (85) auto-organizes with **zero human
eyes**; the only net is the opt-in bulk reident audit run manually later. A
human glance within a day would catch the rare miss — without adding a step to
the happy path. This makes *effective* first-pass accuracy ~100%.

## Goal (from prompts/15 Stage I)

### A. Backend — `GET /api/library/recently-organized?since=48h`

List every file auto-organized in the window, newest first. For each: the file,
its resolved title/author/series, the `Operation.confidence` it was moved at, a
short evidence summary (what the identification was based on — pull from the
file's latest `ai_decisions.reasoning_summary` + whether it had an ISBN /
provider match / `batch_prior` note), and its current status.

- Source: `operations` rows with `action in (move, move_and_rename, rename)`,
  `status = done`, `dry_run = False`, `timestamp >= now - since`, joined to
  `File` → `Book` → author/series. (Confirm the exact `OperationAction` members
  and that `Operation.confidence` is populated by the organize path —
  `organize_service`.)
- `since` accepts `24h` / `48h` / `7d` style; clamp to something sane
  (e.g. ≤ 30d).
- New route file or fold into `app/api/routes/library.py`; schema in
  `app/schemas/`. Mirror the existing route/schema/`api.ts` conventions
  (see `routes/jobs.py` + `frontend/src/services/api.ts` for the shape).

### B. Backend — Confirm / Correct actions

- **Correct** already exists end to end: `POST /api/files/{id}/correct` →
  `file_service.correct_file` + `CorrectFileForm.tsx`. Reuse it — the tray's
  "Correct" opens the same form.
- **Confirm** is new: a lightweight positive signal so Stage 0's corpus can grow
  from real confirmations. Options (pick one, justify):
  - a `Review(status=approved)` row for the file (the `ReviewStatus` enum has
    `approved` — check), **or**
  - a flag on the latest `ai_decisions` row (e.g.
    `raw_response_json["human_confirmed"] = {"at": ...}`).
  Whichever — it must be idempotent, must not move the file, and must be
  queryable later (a `snapshot_book.py` mode or `build_truth.py` could harvest
  confirmed identifications as free ground truth — note the hook, don't build
  the harvester here).
  New endpoint `POST /api/files/{id}/confirm`.

### C. Backend — optional `settings.organize_hold_hours` soft-hold

- New settings key (`app/core/settings_keys.py`), **default `0` = today's exact
  behaviour**. Surface it in the Settings page + a `GET/PUT` like the nightly
  settings pair (`routes/jobs.py` is the template).
- When `> 0`: an auto-eligible file (`status = inbox`, cleared the bar) is **not
  organized** until it has been sitting in `inbox` for that many hours. The
  organize pass (`organize_service.organize_eligible_files` — the shared core
  behind the manual button *and* scan's `_auto_organize`) filters its
  `select(File.id).where(status == inbox, book_id is not None)` by
  `last_processed_at <= now - hold` (confirm `last_processed_at` is set when the
  file lands in `inbox`; if not, use `discovered_at` or add a timestamp).
- **Gotcha:** the hold must not stall the nightly job — held files simply aren't
  eligible *yet*; they flow on the next run. No queue, no cron, just a `WHERE`.
- Dashboard shows the count of currently-held files.
- A correction (or Confirm) in the tray lands *before* any Drive move when the
  hold is on — that's the point.

### D. Frontend — the tray

- A Dashboard panel (`frontend/src/pages/Dashboard.tsx` — it already composes
  `ReviewQueue` / `Duplicates` as collapsible sections; match that pattern).
  "Recently auto-organized (last 48h)" with a row per file: title/author/series,
  a `ConfidenceBar`, the evidence summary, **Confirm** and **Correct** buttons.
- Confirm → `POST /confirm`, row gets a checkmark, optimistic update.
- Correct → opens `CorrectFileForm` (already used on the Library page), same
  flow.
- If `organize_hold_hours > 0`: a second sub-list "Pending (held N h)" with the
  same actions, plus a line on the Dashboard summary.
- `since` toggle (24h / 48h / 7d).

## Gotchas / constraints

- **No AI calls anywhere in this stage.** It's all DB + Drive-metadata reads.
- `hold_hours = 0` must be a genuine no-op — a test that asserts the organize
  pass behaves identically to today when it's unset.
- Don't double-log: a Confirm is not an `Operation`.
- The organize pass runs concurrently (`_ORGANIZE_CONCURRENCY`) and takes the
  process-wide `get_book_write_lock()` for its writes — a Confirm/Correct
  landing mid-organize must be safe (it already is for Correct; keep Confirm on
  the same lock discipline).
- Windows: James tests against a **running deployed-style build**, not the dev
  server reading diffs. He is not terminal-savvy — the Settings control has to
  be one toggle + a number field, not a config-file edit.

## Acceptance

- Panel works against a running app (James verifies): shows the last window's
  auto-organized files, Confirm sticks, Correct opens the existing form.
- `organize_hold_hours = 0` → organize behaviour byte-identical to today
  (test).
- `organize_hold_hours = 24` → a just-organized-eligible file is **not** moved
  on the immediate pass; it is moved on a pass ≥ 24h later (test with a
  monkeypatched clock or a back-dated `last_processed_at`); a Correct in the
  tray before that pre-empts the move (test).
- `GET /api/library/recently-organized` unit-tested for the window filter +
  the evidence summary shape.

## Ship it green

```
cd backend && pytest
cd backend && pytest -m corpus          # must not regress IDENTIFICATION-EVAL.md
cd frontend && npm run build
cd library-viewer && npm run build && npx vitest run && npm run lint   # only if you touched it (you shouldn't)
```

One commit, push. Then:

- Tick Stage I in `prompts/15-identification-accuracy-push.md` Sequencing block +
  its `#### Stage I` section (a `> **DONE …**` blockquote like the others).
- `prompts/README.md` — mark the whole `prompts/15` push complete.
- `ROADMAP.md` — Stage I done; and see prompts/15 §"When the whole push is done"
  (fold out any now-dead code, write a short "identification pipeline, 2026"
  section in `SPEC.md`/`README.md` since §5 is well out of date).
- Append before/after to `IDENTIFICATION-EVAL.md` (this stage's number is again
  flat by construction — the corpus has no organize pass and no Dashboard — say
  so; its value is the human-glance safety net + the confirmation signal for
  future ground-truth growth).
- Update memories `project-bookbrain-identification-accuracy-push` (PROGRESS:
  Stage I done → **whole push complete**) and `project-bookbrain-review-followups`.

## Migration

If B or C adds a column (`ai_decisions` flag is JSON so needs none; a File
timestamp might), it's a plain nullable `op.add_column` — `tests/test_migrations.py`
runs `alembic upgrade head` + `alembic check`, so a schema change without a
migration fails the suite. Follow the recent migration style (prompt 14 D added
one).
