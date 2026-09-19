# Task 46 — Logging housekeeping: dead log file, `status.json` bloat, the torrent question

**Not started.** Small, independent, no dependencies on `prompts/43`–`45`. Safe
to fold into any session already touching these files. From
`LOGGING-REVIEW-2026-09-18.md` findings F9, F10, F6.

Three unrelated tidy-ups plus one decision to put in front of James.

## Part 1 — Retire `backend/nightly-runs.log` (F9)

Last written **2026-09-13 13:32** — 5 days stale at audit time, 66 KB.

Its only writer is `_configure_standalone_logging()` in
`jobs/nightly.py:405-417`, which runs solely under
`python -m app.jobs.nightly`. Verified on the live box: `systemctl
list-timers | grep -i book` and `crontab -l` are both empty. On this Linux box
the **in-process APScheduler job** is what actually runs the nightly, and it
logs to the journal like everything else.

The risk isn't disk space. It's a file called `nightly-runs.log` containing a
complete, plausible, perfectly-formatted nightly summary that is five days
old — exactly what a 3 a.m. investigation opens first and believes.

Decide and implement one of:

- **Delete the file and keep the code.** The standalone entrypoint is still a
  legitimate way to run a nightly by hand, and `README.md:50` documents it
  ("For when the machine's usually not…"). Cheapest, but the file comes back
  the next time anyone runs the CLI, just as stale.
- **Keep both but make staleness obvious** — e.g. the standalone runner writes
  a header line noting it's the CLI path, not the scheduled one. Weak.
- **Delete the file and drop the file handler**, leaving the standalone runner
  logging to stderr only (where it's visible to whoever ran it, and captured by
  the journal if run under systemd). Cleanest, and the file handler's
  `RotatingFileHandler` has no remaining consumer.

Recommendation is the third, but check `README.md:50` and `RESTORE.md` for
anything that reads the file before removing it. Whatever you choose, update
`README.md:50` to match, and check `.gitignore` (it already covers
`backend/nightly-runs.log*` per `prompts/19`).

## Part 2 — Trim `status.json` and fix the poll cadence (F10)

The file is **105,548 bytes** (up from 93 KB when the audit was specced) and is
rewritten every 30 s.

**The bloat:** `bookbrain.providers` alone is **42,741 bytes** — 40 % of the
file. Nearly all of it is `searched_recent`, which carries per provider 5
entries, each with the full `candidate` object *and* an `alternatives` array of
full candidate objects (opaque `full` handle, title, author, format, size,
server, score, provider). Example: one entry's `alternatives` held four
complete candidates including 200-character `!Bot Author - [Series 01] - Title
(re-release) (epub).rar` handles.

The mobile page renders **three** fields from each entry — title, author,
status (`renderProviderCard` → `renderList`, `index.html:455-465`). The server
dashboard doesn't read `alternatives` either.

Fix: stop serialising `alternatives` (and the `full` handle) into
`searched_recent`. Check both readers before cutting — `dashboard.sh`'s `jq`
filters and `mobile/index.html` — and cut only what neither uses. Expect the
file to drop to roughly 60 KB.

Not a leak: `searched_recent` is sliced to 5 and the queue fields are counts,
so it isn't growing unboundedly. This is bloat, not a bug.

**The cadence:** `mobile/index.html:468` polls every **15 s**
(`setInterval(refresh, 15000)`); `dashboard.sh:7` rewrites the file every
**30 s** (`FAST_REFRESH=30`). Half of every phone's fetches pull
guaranteed-identical bytes, and `cache: 'no-store'` (`:337`) means none can be
served from cache. An open phone tab pulls ~25 MB/hour over Tailscale, about
half of it redundant.

Match the poll to the write (30 s), or keep 15 s and make it conditional — a
`HEAD` for `Last-Modified`, or keep `no-store` but compare `generated_at`
before re-rendering. Simply aligning to 30 s is the honest fix; `no-store` is
correct and shouldn't be removed.

> If `prompts/45` has already landed, it may have done the cadence half of this
> (it's listed there too). Check first; don't do it twice.

## Part 3 — A decision for James: is the torrent provider earning its keep? (F6)

Not a code change — a question to put in front of him with the numbers.

From `status.json` and confirmed against `acquisition_candidates`:

```
provider     status      count
torrent      failed       2210
torrent      approved        4
```

**Four successes against 2,210 failures — a 0.18 % success rate, all-time.**
For comparison: openbooks 124 approved / 7 failed, libgen 99 approved / 40
failed.

The torrent provider also accounts for a visible share of the journal's
non-noise lines — 31 × `torrent: … released early (stalled with N B/s
throughput, likely no seeders)` and 26 × `released early (no usable file
arrived in time)` in a single 24 h window, plus 2 × `torrent submit tick
failed` with tracebacks.

This is not a bug report — the provider may be doing exactly what it was
designed to do (try the long tail that the other two miss, cheaply, and give
up fast). Four books nobody else could find might be worth it. But a
99.8 %-failing subsystem generating steady log volume and 2,210 DB rows should
be a deliberate choice, not an unnoticed default.

Present the numbers, ask, and do whatever he says. If it stays, consider
whether the `released early` lines belong at INFO or DEBUG given they're the
expected outcome 99.8 % of the time.

## Constraints

- Production box (`/opt/bookbrain` on `homeserver`).
- `dashboard.sh` rewrites `status.json` in a live loop — edit the script, not
  its output.
- Backend tests before committing.
- Parts 1, 2 and 3 are fully independent; ship whichever you finish.
