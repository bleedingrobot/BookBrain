# Task 45 — One red thing: an alerts block that survives the night

**Not started.** `dashboard/dashboard.sh` + `dashboard/mobile/index.html`, plus
a small `main.py`/health touch. From `LOGGING-REVIEW-2026-09-18.md` Part 3 and
findings F5, F7, F8.

Run **after `prompts/43` and `44`** — this task consumes both (the per-provider
signal from `44`, and `43`'s root-logger fix is what makes "any traceback in the
last hour" a precise query rather than a guess).

## The question this answers

*If BookBrain breaks at 3 a.m., what tells James, and how long does it take?*

Today: **nothing.** He finds out when he notices books stopped arriving. For
the failure that actually happened this week that took 18 hours, and only
because he happened to look.

The problem isn't that signals are missing — four of the six main failure modes
*do* produce a visible signal. It's that **every signal in the system is
pull-based**: it needs a human looking at a screen at the moment the thing is
wrong. tty1 is unwatched at 3 a.m. and the phone is asleep. Nothing survives
until morning saying "at 03:14 this went bad."

## Part 1 — An `ALERTS` block at the top of the BookBrain section

One block in `dashboard.sh`, rendered above the existing BookBrain header. One
red line per active condition; a single green `no alerts` when there are none.
Every input below already exists or comes from `prompts/44`.

Conditions for day one:

1. **Backend silent > 5 minutes.**
   `journalctl -u bookbrain.service -n 1 -o short-unix` → compare to now.
   The 5-minute threshold is measured, not guessed: over the 7-day journal the
   p99 inter-line gap is **31 seconds** and the only silences longer than
   5 minutes were the 09-18 hang (14.3 min) and two install-day gaps on 09-12.
   **This threshold depends on the uvicorn access log staying on** — see
   `prompts/43` Part 3. If someone later turns it off, this alarm dies, because
   `app.*` alone has a routine 16-minute gap. Put that in a comment here too.
2. **A provider is dead** — 0 successes in 6 h while another provider is
   succeeding. From `prompts/44`.
3. **`status.json` is stale** — older than 3 × `FAST_REFRESH` (90 s). This one
   detects `dashboard.sh` itself having wedged on a `curl`, so compute it from
   the file's mtime in the *reader*, not the writer.
4. **A traceback in the last hour.**
   `journalctl -u bookbrain.service --since '1 hour ago' | grep -c Traceback`.
   Cheap, and precise once `43` has landed. Worth showing the count — there
   were 202 in the last 7 days and 32 `database is locked` among them, so
   expect this to be non-zero on day one. If it's permanently red it's useless;
   tune the condition (or fix the DB locking) rather than deleting the alarm.
5. **Nightly job status ≠ success.** Already fetched (`NIGHTLY_STATUS`), just
   promote it into the block.

Keep it boring: a fixed-width red line per condition, newest state only. The
existing `status_color` / `cline` / `hr` helpers are right there.

## Part 2 — Make it survive the night

This is the part that actually changes the answer to the opening question.

Append each alert **transition** (not each tick — 2,880 ticks a day) to
`dashboard/alerts.log`, one timestamped line per state change:

```
2026-09-17T18:04:11  RAISED   provider-dead: openbooks 0/47 in 6h
2026-09-18T11:43:02  CLEARED  provider-dead: openbooks
```

Render the last 3 lines under the alerts block. That converts "a red thing
nobody was awake to see" into "at 18:04 OpenBooks stopped, at 07:00 it was
still stopped" — readable at breakfast, which is the entire point.

Needs a tiny bit of state across loop iterations (the previous tick's active
set) — a variable in the loop, or a small file next to the log. Rotate or
truncate it (it's one line per transition, so this is a slow-growth file, but
don't let it run forever). Add it to `.gitignore` alongside the other runtime
artifacts.

**Do not add push notifications in this task.** A `ntfy`/Pushover call on
transition-to-red is a two-line addition later, but doing it before the
conditions are tuned would train James to ignore his phone. Get the alerts
accurate first; `alerts.log` is how you find out whether they are.

## Part 3 — Wire up the staleness the mobile page already computes (F7)

`mobile/index.html`:

```js
let lastGeneratedAt = null;        // :332
...
lastGeneratedAt = d.generated_at;  // :344
```

Those are the **only two occurrences in the file** — the variable is declared,
assigned, and never read.

The gap that leaves: `dashboard.sh` dies while
`bookbrain-dashboard-mobile.service` keeps serving the last `status.json` it
wrote. The phone's `fetch` succeeds, so the existing catch-block banner never
fires, and the page renders a full set of confident green pills off an
arbitrarily old file. The only hint is `updated 4 hours ago` (`:347`) in
ordinary subtitle styling.

Fix: if `now - generated_at > 3 × 15 s`, add the existing `.stale` class to the
subtitle and grey the gauges. The `.stale` class and the `ago()` helper both
already exist — this is about six lines.

While you're in there, fix the poll/write mismatch (F10, also in `prompts/46` —
do it in whichever session gets there first, not both): the page polls every
15 s (`setInterval(refresh, 15000)`) against a file rewritten every 30 s
(`FAST_REFRESH=30`), with `cache: 'no-store'`, so half of every phone's
fetches pull ~105 KB of guaranteed-identical bytes over Tailscale.

## Part 4 — Stop `api: ok` overclaiming (F8)

`app/api/routes/health.py`, in full:

```python
async def health() -> dict[str, str]:
    return {"status": "ok"}
```

It touches no database, no Drive credential, no scheduler, no provider. As a
*liveness* probe it's genuinely good — a blocked event loop can't serve even a
literal, so it would have caught the 09-18 hang. But it renders as `api: ok` in
green as the most prominent thing on the dashboard, and it cannot detect a
wedged scheduler, expired Drive credentials, a locked database, or a dead
provider.

**Do not build a heavyweight health aggregator.** The fix is presentational:
once Part 1's alerts block sits above it, `api: ok` is bounded by context and
reads correctly. Consider relabelling it (`api: responding`) so the word `ok`
stops carrying weight it hasn't earned. If you do add any real check to the
endpoint, it must not become a slow or failure-prone call — it's polled every
30 s and `curl -m 2` will happily report a slow health check as a dead server.

## Constraints

- Production box; `bookbrain-dashboard.service` runs `dashboard.sh` in a live
  loop on tty1. Editing the script is in scope; hand-editing `status.json`
  is not.
- The mobile page is served tailnet-only on port 443. **Never** expose it via
  Tailscale Funnel — there's an unauthenticated web terminal on that port
  (`bookbrain-dashboard-web.service`, ttyd). See the infra note in memory.
- Verify the silence alarm without breaking anything: don't stop
  `bookbrain.service` to test it. Test the threshold logic against a fake
  timestamp, or against the known 09-18T09:33:44→09:48:01 gap in the journal.
