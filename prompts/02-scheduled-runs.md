# Task 2 — Scheduled unattended runs

Read `prompts/README.md` first for shared context.

## Why

BookBrain's tagline is "auto-organizing" but every step is a manual button in the
admin frontend: check Torrents folder → copy to Book Dump → Start scan → Review →
Clear duplicates → Organize → Generate covers → Refresh viewer data. The pipeline
is already designed as idempotent functions (SPEC §3: "designed as an idempotent
function so a scheduler can call it later"). Make that real.

## Goal

A **nightly run** that, with no human present, does:

1. Scan the Drive inbox (Book Dump) — `ScanService.run_scan`
2. Auto-organize everything that cleared the confidence threshold — this already
   happens inside `run_scan` via `_auto_organize` when `dry_run` is off
3. Regenerate covers for anything new (`CoverService`, bounded pass is fine)
4. Regenerate the library index sidecar (`library_index_service`)
5. Optionally: pull the local Torrents folder into Book Dump first
   (`local_scan_service`) so torrented books get picked up too

Anything uncertain still lands in the review queue for James to handle later —
the nightly run must **never** auto-resolve a review or auto-clear a duplicate.

## Design — decide and justify, but here's the lean recommendation

The backend runs on James's Windows machine and isn't up 24/7. Two layers:

**A. In-process scheduler (APScheduler `AsyncIOScheduler`)** — fires the nightly
job if the server happens to be running at that hour. Cheap, no OS config.
Register it in `app/main.py` lifespan. Gate it behind a setting
(`nightly_run_enabled`, `nightly_run_hour`) in the `settings` table or `.env`.

**B. A standalone entrypoint** — `python -m app.jobs.nightly` (or
`backend/scripts/nightly.py`) that runs the same job function **without the HTTP
layer**, exits non-zero on failure, logs to a file. This is what a Windows
Scheduled Task calls. Provide the Task Scheduler setup as a committed
`.xml` you can import, or a tiny `register_nightly_task.ps1` that James can
double-click — remember he's not terminal-savvy, so the setup has to be
one action, not a tutorial.

Share ONE job function between A and B — e.g. `app/jobs/nightly.py::run_nightly()`
taking an `AsyncSession` + `Credentials`. The credentials come from the same
encrypted token store the API uses (`auth_service`); if the refresh token is dead,
the job logs a clear "reconnect Google in Settings" and exits, doesn't crash.

## Job durability (fold in if cheap, else note for later)

Scan/organize job status lives in an **in-memory dict** (`ScanService._jobs`), so
a server restart mid-job strands the UI. A scheduled run doesn't need the UI job
tracker at all — it just calls the service functions — but consider persisting a
small `job_runs` table (started_at, kind, status, summary, error) so:
- the nightly run leaves an audit trail James can see in the morning
- the Activity page / Dashboard can show "last nightly run: 3 organized, 2 to review"

If persisting full job state is too big a yak-shave, at least append a
`nightly-run` entry to the existing activity log with a summary.

## Frontend

- Settings page: toggle + hour picker for the nightly run, backed by the new
  settings.
- Dashboard: a line showing the last nightly run's result + timestamp.

## Acceptance criteria

- `run_nightly()` exists, is covered by a test (mock Drive + AI), and is called by
  both the APScheduler job and the standalone entrypoint.
- Running the standalone entrypoint against the real app (servers down, DB free)
  does a full scan→organize→covers→index pass and exits 0.
- Nothing in the review queue or duplicates list is ever auto-resolved.
- A dead Google token produces a clean logged message, not a traceback.
- Settings toggle works; Dashboard shows last-run info.
- `cd backend && pytest` green; `cd frontend && npm run build` green.
- New dependency (APScheduler) added to `pyproject.toml` and the lock/install
  documented in README.
- Committed and pushed. ROADMAP + SPEC updated (this closes the "scheduler later"
  item). Add a memory note.

## Gotchas

- Don't run the nightly job while a manual scan/organize is in flight — reuse
  whatever `_write_lock` / job-in-progress guard the services already have, or add
  a coarse "a pipeline run is active" flag.
- APScheduler + `uvicorn --reload`: the reloader spawns a child process; make sure
  the scheduler starts in the worker, not the reloader parent, or it fires twice.
- Long job under `--reload` can wedge the dev server (known issue) — the standalone
  entrypoint sidesteps this, which is part of why it exists.
- Timezone: use the machine's local time for "2am", be explicit about it.
