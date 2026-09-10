# Task 37 — OpenBooks "Find a Book" acquisition helper (experimental)

**Shipped 2026-09-10.** James: "lets give it a try and see how it performs."

## What it is

A manual acquisition tool in the **admin app only**. Type a title/author →
search OpenBooks (an IRC client for the `#ebook` channel on IRC Highway) →
pick a result → the backend downloads it and uploads it straight into the
Drive inbox ("Book Dump"). From there the existing scan → identify → organize
pipeline runs unchanged — nothing here identifies or renames anything.

One book at a time, operator in the loop — no unattended downloading.

### "Books to get" — the batch queue (added + reworked 2026-09-10)

The Find a Book page has a **"Books to get"** panel over `acquisition_candidates`.
Three sources, merged and deduped by book (ISBN, else title+author) in
`_gather_targets`, minus anything already an organised file:

1. `bookbrain-wishlist.json` items still `wanted` (`source="wishlist"`)
2. `bookbrain-reading.json` `wantUnowned` — un-owned Hardcover want-to-read
   (`source="want_to_read"`, `request_id="wtr:<key>"`)
3. `bookbrain-lists.json` `candidates` — curated-list picks
   (`source="list"`, `request_id="list:<key>"`)

- **Search 25 more** (`POST /api/acquire/requests/refresh?limit=25`) searches
  OpenBooks for the next N un-searched targets (~11s apiece, capped 1..100),
  keeps the best **EPUB** matches + up to 6 alternatives, for a manual "Get".
  Repeat, or let the nightly (`limit=50`) grind through — **the nightly
  acquire step only runs when auto-get is off** (with auto-get on it does its
  own fresh searches). The job reports `outstanding` so the UI can say "N
  still to search".
- **Get** per row → downloads into the inbox via `acquire_service`; a wishlist
  row also flips its item to `sourced` (best-effort sidecar RMW). Alternatives
  + skip as before.
- **Auto-get** (toggle on the panel, `settings` key `openbooks_autoget_enabled`,
  off by default): `scheduler._run_scheduled_autoget` → `acquisition_service.autoget_tick`
  every 60s, only when idle (OpenBooks up + `openbooks_service.is_busy()` false
  + no scan + no refresh job). **Search-fresh-then-get** (reworked 2026-09-11 —
  James: "not pre-searching anything… so it's always fresh and current"): each
  tick picks the next `_gather_targets` book that's **due** (no row yet, or its
  backoff elapsed), **searches OpenBooks for it right then** and downloads the
  best EPUB **≥ 0.9** immediately — the `!server file` command is always
  seconds old. On a *validation* failure (stub / corrupt) it tries the next
  candidate, up to `_AUTOGET_INTICK_TRIES = 3`; on a *transient* failure (75s
  no-deliver, dead server) it stops and lets the backoff carry it. Backoff:
  `_AUTOGET_RETRY_AFTER = 30 min`, then `_AUTOGET_STUBBORN_BACKOFF = 6 h` once
  a book's been failing `> _AUTOGET_STUBBORN_AFTER = 2 h` (measured by row
  `created_at` — no schema change), `_AUTOGET_NOMATCH_BACKOFF = 7 days` for a
  searched-but-nothing-found book. A momentary empty re-search **keeps** the
  previous match (`_upsert(preserve_existing=True)`) rather than wiping it to
  `no_match`. `acquisition_candidates` is now an **attempt log**, not a
  pre-searched queue. **When the inbox hits `_AUTOSCAN_INBOX_THRESHOLD = 20`**
  it fire-and-forgets a scan (the next tick's `has_running_job()` guard pauses
  auto-get until it's done). Dropped in the rework: the `_AUTOGET_SEARCH_BATCH`
  refill and the `_AUTOGET_RESEARCH_AFTER` staleness re-search (both moot —
  every tick searches fresh). `PUT /api/acquire/autoget` toggles it and
  re-syncs the APScheduler `IntervalTrigger` job.
- `list_requests` calls **`_dedupe_candidates`** (one book on wishlist +
  want-to-read + a list → one row, keeping the wishlist id) then
  **`_prune_now_in_library`**: an `approved` row whose title+author now matches
  an organised book is deleted (self-cleans the want-to-read / list rows;
  wishlist rows also drop once the viewer reconciles the item to `acquired`).
  It also suppresses the synthetic `unsearched` row for a wishlist book that
  already has a candidate row under another id.

`GET /api/acquire/suggestions` still exists (reads the two sidecars raw,
read-only) but the frontend `<Suggestions>` browse was removed — the batch
queue covers it. `AcquisitionCandidate.source` column (migration
`30ba28826073`).

The library-viewer can't do any of this itself (static site, no backend,
localhost-only OpenBooks). Admin-only.

## Running it

- `openbooks.exe` is gitignored — download once from
  https://github.com/evan-buss/openbooks/releases (the `openbooks.exe` asset)
  into `backend/tools/`.
- `backend/.env`: `OPENBOOKS_ENABLED=true` (default false), plus
  `OPENBOOKS_WS_URL` / `OPENBOOKS_DOWNLOAD_DIR` / `OPENBOOKS_BINARY` if you
  want to override the defaults.
- The **Find a Book page has a Start/Stop button** — it spawns
  `openbooks.exe server --port 5228 --persist --no-browser-downloads --dir
  <OPENBOOKS_DOWNLOAD_DIR>` (the same command as
  `backend/tools/run-openbooks.ps1`, which is still there if you'd rather run
  it by hand). A clean backend shutdown stops a server it started.
- **Don't open the OpenBooks web UI** (`http://localhost:5228`) while BookBrain
  is using it — the server allows exactly one WebSocket client and BookBrain
  holds it.

When OpenBooks isn't running the "Find a Book" page still loads and search
returns a clean "can't reach OpenBooks" error.

## Shape

### Backend

- `app/services/openbooks_process_service.py` — Start/Stop the OpenBooks
  server as a child process. `start()` spawns it and waits for the port,
  `stop()` terminates the child (or kills an orphan holding the port via
  `netstat` + `taskkill`), `status()` → `{installed, running, managed, pid}`,
  `shutdown()` (lifespan) only ever touches a child we started. Endpoints
  `GET/POST /api/acquire/server{,/start,/stop}`.
- `app/services/acquisition_service.py` — the open-requests flow.
  `refresh_candidates` (read `bookbrain-wishlist.json` → search each `wanted`
  item, EPUB-only, `score_candidate` handles OpenBooks swapping author/title,
  dedupe, upsert `AcquisitionCandidate` with top-6 alternatives, skip rows
  already `approved`/`skipped`/`pending`), `approve_request` (→
  `acquire_service` + `_mark_wishlist_sourced`), `skip_request` /
  `reset_request`, `list_requests`. `AcquisitionCandidate` model +
  `ee2155ba2aa5` migration, keyed on the wishlist item id. Routes
  `GET /api/acquire/requests`, `POST /api/acquire/requests/refresh` (job +
  `…/refresh/{job_id}` poll), `POST /api/acquire/requests/{id}/{approve,skip,reset}`.
  Nightly `_acquire_candidates_phase` runs the search step.
- `app/services/openbooks_service.py` — one cached, lock-serialised
  `websockets` client. Protocol (from `server/messages.go`): request
  `{"type": N, "payload": {...}}` — CONNECT=1, SEARCH=2, DOWNLOAD=3; the
  server caps an incoming frame at 512 bytes. `search(query) -> SearchOutcome`
  (`results: [BookResult]`, `parse_errors`, `message`). `download(full) ->
  Path` — `full` is the raw `!server filename` string from a result, passed
  back verbatim. Reconnects once on a mid-op drop. Exceptions:
  `OpenBooksUnavailable` (503), `OpenBooksRateLimited` (429, carries
  `wait_seconds` — the server's own >=10s search cooldown), `OpenBooksError`
  (502).
- `app/services/acquire_service.py` — `acquire_to_inbox(full, filename,
  provider, inbox_folder_id)`: download → size/format sanity (`is_supported_ebook`
  / `is_convertible`, real-EPUB zip check) → `provider.upload_new_file(...)`
  into the inbox → delete the local copy on success.
- `app/api/routes/acquire.py` — `GET /api/acquire/status`,
  `POST /api/acquire/search {query}`, `POST /api/acquire/download
  {full, filename}` (the download route needs a live Drive connection +
  a configured inbox folder).
- `app/schemas/acquire.py`; registered in `app/api/router.py`; connection
  closed in `main.py` lifespan shutdown. `websockets` added to
  `pyproject.toml` (was transitive via `uvicorn[standard]`).

### Frontend

- `src/pages/Acquire.tsx` — route `/acquire`, nav "Find a Book". A
  `<ServerControl>` strip (●/○ status + Start/Stop, polled), then the search
  box, "EPUB only" toggle + free-text filter, results table sorted by
  preferred format, per-row "Get" button with inline progress/result.
  Disabled-state card when `status.enabled` is false.
- `src/types/acquire.ts`, `api.ts` (`acquireStatus` / `acquireSearch` /
  `acquireDownload`).

### Tests

- `tests/test_openbooks_service.py` — a real local `websockets.serve` fake
  scripts the protocol: parse results, rate-limit → `OpenBooksRateLimited`,
  no-results → `message`, download → local path, server error, nothing
  listening → `OpenBooksUnavailable`, oversized query, mid-op drop → one
  reconnect. `conftest._reset_shared_singletons` resets the module client
  (it owns an `asyncio.Lock`).
- `tests/test_acquire_service.py` — fake provider: happy path + local cleanup,
  unsupported format rejected, corrupt `.epub` rejected, filename
  sanitisation.

## Gotchas / notes

- **OpenBooks v4.5.0 crashes on `panic: send on closed channel`**
  (`server/irc_events.go:71`) when a DCC transfer completes *after* the WS
  client disconnected — which is exactly what a flaky source server + the
  download timeout + a backend restart produce. After it, IRC re-joins fail
  ("Unable to connect to IRC server") until every `openbooks.exe` is killed
  and it's started fresh. Mitigations: `score_candidate` down-ranks servers
  that send truncated files (unknown server / `N/A` size) so "Get this" never
  auto-picks the crash trigger; `_DOWNLOAD_TIMEOUT` is 150s not 300s.
- **The `@search` bot on `#ebook` goes down.** OpenBooks' own advice is to use
  `searchook` instead — `OPENBOOKS_SEARCHBOT` (config, passed as `--searchbot`,
  takes effect on the next Start). Default `search`.
- OpenBooks keeps a **fixed IRC nick** (its `--name`, set once at launch) and
  reconnects to IRC on every WS-client (re)connect. Rapid reconnect churn with
  one nick can get throttled by IRC Highway (searches then silently time out).
  The service holds **one** long-lived connection, so in normal use this
  doesn't bite — but if searches start hanging, restart the server (Stop/Start
  on the page, or `run-openbooks.ps1`) for a fresh random nick.
- OpenBooks' search-result parser sometimes swaps author/title. Doesn't
  matter — the identify pipeline re-derives everything from the EPUB.
- `#ebook` is a book-piracy channel. This is a manual, per-book operator tool
  in the localhost admin app; it is never exposed in the family
  library-viewer.
- Not browser-verified as a full round trip (search + download were
  live-verified via the WS API and produced a valid EPUB on disk; the inbox
  upload leg is unit-tested only — it needs live Drive creds).

## If it doesn't perform well

Everything is behind `OPENBOOKS_ENABLED` (default `false`). Setting it back to
`false` fully hides the feature; the `openbooks.exe` process is independent and
just stops being contacted.
