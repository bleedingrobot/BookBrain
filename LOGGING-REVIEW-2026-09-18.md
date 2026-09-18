# BookBrain — logging + dashboard audit, 2026-09-18

Read-only audit following `prompts/42-logging-and-dashboard-audit.md`. Nothing
was edited, restarted, or deployed. `bookbrain.service` and
`bookbrain-dashboard.service` ran untouched throughout; `status.json` was read,
never written. All counts below were measured live on `homeserver` between
12:00 and 12:15 NZST.

Output: this file + 4 work-prompts (`prompts/43`–`46`) + the README table
update. Running order and rationale at the end.

**Headline:** the audit's premise — "the information existed but wasn't
surfaced" — is right, but the two worked examples are both worse than the
prompt describes, and one of them is a different incident than the one written
down. The single most useful number in this document is that
**`journalctl -u bookbrain.service | grep ERROR` returns 1 line for the last 7
days, during which 202 tracebacks were printed.**

---

## 1. What's already correct

Genuinely fine, verified, and not worth another session's attention.

- **`main.py`'s `app` logger config is right, and its comment is accurate.**
  `propagate = False` + an explicit `StreamHandler` + INFO on the `app`
  namespace does exactly what the comment says, and scoping it to `app` rather
  than the root logger really does keep httpx's per-request INFO line out of
  the journal. The past bug it documents (commit `86de4cf`) is fixed. F1 below
  is the *other half* of that same problem, not a regression in this half.
- **The 2-second LLM-tagging interval is deliberate, documented, and not a
  bug.** `scheduler.py:92-99` explains it: tiny interval + `max_instances=1` +
  `coalesce` is a back-to-back-as-fast-as-Ollama-responds idiom, so APScheduler
  refires the instant the previous tick frees up. The 25,836 "skipped" lines
  are the *expected* output of a correct design. This is a log-level problem
  (F4), not a scheduling problem — don't let anyone "fix" the interval.
- **The dashboard's health panel does go red during a hang.** `HEALTH_OK` is
  assigned without a fallback (`dashboard.sh:297`), and `jq -r '.status //
  "unreachable"'` on *empty* input emits nothing rather than `unreachable` — but
  both use sites apply `${HEALTH_OK:-unreachable}` (`:461`, `:551`), and
  `status_color` maps `unreachable` to red. Verified by running the exact
  pipeline against a dead port. The header would have read
  `service: RUNNING`(green) `api: unreachable`(red).
- **Journal retention is fine; the 170 MB is not a problem.** `journald.conf`
  is stock, so `SystemMaxUse` resolves to the 4 G cap (confirmed in
  journald's own startup line: *"System Journal … is 24M, max 4G"*). BookBrain
  writes ~28 MB/day, so the effective window is roughly **145 days**, not the
  6 days currently on disk — that 6 days is just the box's age (first entry
  2026-09-12). No BookBrain-side retention policy is needed.
- **Nothing is dropping BookBrain's log lines.** The 3,400-odd `[RATELIMIT] …
  (N dropped)` entries in the journal are **Tailscale's own internal**
  rate-limiter on `open-conn-track` messages, not journald's, and not
  BookBrain's. BookBrain averages ~0.6 msg/s against journald's default 10,000
  per 30 s — three orders of magnitude of headroom.
- **The viewer Dashboard already tells the truth about its own freshness.**
  `DashboardScreen.tsx:285` renders *"Snapshot from {timeAgo(generatedAt)} —
  refreshes nightly and after every …"*, and `lib/dashboard.ts:135` parses
  `generatedAt` defensively (`typeof … === 'string' ? … : null`) with
  `hasQueue` gated on it being non-null. The prompt's worry that a
  hours-stale snapshot is served as plausible-looking fact is **unfounded** —
  it's labelled. (The *server* mobile page is the one with this bug; see F7.)
- **The mobile page handles an unreachable server correctly** — the `fetch`
  catch writes a `.stale`-classed *"Can't reach the server — retrying…"* banner
  (`mobile/index.html:338-341`). It's the *stale-but-served* case that's
  missing, not the unreachable case.
- **The OpenBooks JOIN-race fix is verified working.** Since the 11:41:45
  restart that carried `baa1af2`: **zero** `openbooks search failed` lines and
  **22** successful `auto-got` downloads in the first 30 minutes — against a
  prior baseline of 7–8 failures/hour sustained for 18 hours. This one is
  genuinely fixed, not quiet.
- **The dashboard's fast/slow tier split is well-judged.** The 20 s timeout on
  `/api/acquire/requests` and the comment above it (`dashboard.sh:314-323`)
  document a real measured failure (4.3 s call against a 5 s timeout causing
  stale-value flicker). That reasoning is sound and the fix is correct.

---

## 2. Findings

### P1

#### F1 — Errors are structurally invisible in the journal: 202 tracebacks, 1 greppable ERROR line

This is the finding that explains all the others.

`main.py` configures the **`app`** logger namespace only, and sets
`propagate = False`. The **root logger is never configured anywhere in the
server** — the only `root.addHandler` calls in the codebase are in
`jobs/nightly.py:414-417` and `jobs/backup_job.py:117-120`, both inside
`_configure_standalone_logging()`, which only runs under the standalone CLI
entrypoints.

So every **non-`app.*`** logger — `apscheduler.*`, `sqlalchemy.*`,
`websockets.*`, `httpx` — has no handler anywhere in its chain and falls
through to Python's `logging.lastResort`: a bare `StreamHandler` at WARNING
with **no formatter**. Its output has no timestamp, no level, and no logger
name:

```
Job "OpenBooks auto-get (trigger: interval[0:06:00], next run at: 2026-09-14 18:13:13 UTC)" raised an exception
Traceback (most recent call last):
  File "/opt/bookbrain/backend/app/services/openbooks_service.py", line 198, in _with_reconnect
    return await op()
```

Compare a real `app.*` line, which is fully formatted:

```
2026-09-18 08:41:59,531 WARNING app.services.acquisition_service: acquire: openbooks search failed for 'The Autumn Republic Brian …
```

Measured over the last 7 days of `bookbrain.service`:

| | count |
|---|---|
| `Traceback (most recent call last):` | **202** |
| tracebacks preceded by an `app.*` `ERROR`/`WARNING` line | **1** |
| lines matching `ERROR app.` | **1** |

And in the 24 h window, of 3,781 non-noise lines, **3,283 (87 %) carry no
level token at all** — they're unformatted traceback bodies.

Concretely, this is what's hiding in there right now:

- `sqlite3.OperationalError: database is locked` — **32 occurrences**
  (14 on 09-15, 18 on 09-17), each with a full SQLAlchemy/aiosqlite traceback,
  **none** logged at ERROR by the app.
- `RuntimeError: Drive download failed: The read operation timed out` — 2.
- `Job "…" raised an exception` — 5 in 24 h, across OpenBooks auto-get, Libgen
  auto-get, and LLM tagging.

**Why it matters:** the first thing anyone does when investigating "BookBrain
broke overnight" is `journalctl -u bookbrain.service | grep -i error`. Today
that returns one line for the whole week, which reads as "no errors". The
errors are all there — they're just unlabelled, untimestamped, and therefore
unfindable and un-time-correlatable. When the `database is locked` errors
happened cannot be established from the journal without reading the
surrounding raw text by hand, because the traceback lines carry no time of
their own (only journald's envelope timestamp, which `-o cat` strips and
which doesn't survive into any grep-based workflow).

**Fix**: attach the same formatter to the root logger, and set the root level
to WARNING so third-party INFO chatter (httpx) still stays out. That is a
~4-line change in `main.py` and it makes every library error timestamped,
levelled, and greppable, without changing what gets logged. Covered by
`prompts/43`.

#### F2 — A dead acquisition provider is indistinguishable from a quiet one; OpenBooks was ~dead for 18 hours and no panel moved

The prompt describes this as "~1h40m (and intermittently for two days
before)". The data says it was worse and simpler than that: **a continuous
18-hour failure that started at 2026-09-17 ~18:00 and never self-recovered**,
ending only when `baa1af2` was deployed at 11:41 today.

`openbooks search failed` per hour, from the journal (7-day window, hours with
any failures):

```
09-16T10  4      09-17T22  8      09-18T04  7
09-16T16  2      09-17T23  7      09-18T05  6
09-16T18  6      09-18T00  7      09-18T06  5
09-17T07  2      09-18T01  8      09-18T07  3
09-17T18  7   ┐  09-18T02  7      09-18T08  6
09-17T19  7   │  09-18T03  7      09-18T09  4
09-17T20  7   │  unbroken          09-18T10  9
09-17T21  7   ┘                    09-18T11  5
```

Before 09-17T18 the failures are sporadic (4, 2, 6, 2 — spread over two days).
From 09-17T18 onward they are a solid block at the job's full tick rate
(OpenBooks auto-get is a 6-minute interval = 10 ticks/hour; 7–8 of every 10
failed). That is not "intermittent". That is off.

**The reason nobody noticed is the important part.** Successful downloads per
6-hour bucket, per provider, from `acquisition_candidates` (status `approved`,
by `resolved_at`):

```
bucket            openbooks   libgen  torrent
2026-09-14 18h            7        0        0
2026-09-15 00h            7        0        0
2026-09-15 06h            5        0        0
2026-09-15 12h            4        0        0
2026-09-15 18h            3        0        0
2026-09-16 00h            4        7        0
2026-09-16 06h            0        6        0
2026-09-16 18h            4       15        2
2026-09-17 00h            3       13        1
2026-09-17 06h            0        4        0
2026-09-17 12h            0        7        1
2026-09-17 18h            1       40        0     <- openbooks dead, total is a RECORD
2026-09-18 00h            1        7        0
```

OpenBooks collapses from 3–7 per bucket to 0–1, and **Libgen more than covers
the shortfall** — the 09-17 18h bucket is the single best 6-hour period in the
entire table. Every aggregate the dashboard shows (`organized last 24h`,
`organized last 7d`, the sparkline, `hit rate`, `Last book downloaded via
auto-get`) was *at or above normal* while one of three providers was totally
dead. There is no panel anywhere that could have moved.

This also means the "log silence" instinct is wrong for this class of failure —
the failures were being logged, once each, at WARNING, 7 times an hour, all
night, into a stream carrying 2,600 lines an hour.

**Fix**: a per-provider **recent** success/failure signal on the server
dashboard — "OpenBooks: 0/47 in 6h" in red. The data is already being
collected (see F6 for why the existing counters can't do this). Covered by
`prompts/44`.

### P2

#### F3 — `acquire: auto-got … via None` — the provider is never named, and it blocks measuring F2

Confirmed, and more pervasive than the prompt's note suggests: of the 82
`auto-got` lines in 24 h, **81 say `via None`** and exactly one names a source
(`via Ook`, an OpenBooks IRC bot nick). Across the full journal the string
`via None` appears **94** times.

`acquisition_service.py:1466`:

```python
logger.info("acquire: auto-got %r (%s) via %s", title, result.get("filename"), cand.server)
```

`cand` is an `AcquisitionResult`, whose `server` field is **OpenBooks-specific**
(the IRC bot that serves the file) and is `None` for every other provider. The
field that actually names the provider is right there on the same dataclass:

```python
# app/providers/acquisition/types.py
server: str | None
...
provider: str = "openbooks"   # <- this one
```

Note the trap for whoever fixes this: the local variable `provider` in scope at
line 1466 is the **`DriveProvider`** (the upload destination), not the
acquisition source. The correct expression is `cand.provider`. The same
mistake is in the failure line at `:1475` and in both return dicts (`:1473`,
`:1477`).

**Why it's more than cosmetic:** this is why F2 had to be measured against the
SQLite table rather than the log. The log records 82 successful acquisitions in
24 hours and cannot say which provider supplied a single one of them. During an
18-hour outage of one of three providers, the success log was structurally
incapable of showing it. Covered by `prompts/44`.

#### F4 — 93 % of the log is noise, and the biggest single source can be silenced surgically

Re-measured over 24 h, matching the prompt's figures almost exactly:

| | lines | share |
|---|---|---|
| total `bookbrain.service` lines | 54,427 | 100 % |
| `… skipped: maximum number of running instances reached (1)` | 25,836 | **47.5 %** |
| HTTP access lines | 24,810 | **45.6 %** |
| everything else | 3,781 | **6.9 %** |

BookBrain is **267,334 of the machine's 352,559 total journal lines — 76 % of
everything `homeserver` logs.**

The good news is that the skip message can be removed without losing anything
real, because APScheduler puts it on a **different logger from job errors**:

- `apscheduler.scheduler` (`schedulers/base.py:900-903`) logs, at WARNING,
  exactly two things: *"Error getting due jobs from job store"* and
  *"Execution of job … skipped: maximum number of running instances reached"*.
  Everything genuinely serious on this logger is ERROR or `.exception()`
  (executor lookup failure, job submission failure, listener errors).
- `apscheduler.executors.<alias>` (`executors/base.py:145,195`) is what logs
  `'Job "%s" raised an exception'` — and it's untouched by anything done to
  `apscheduler.scheduler`.

So `logging.getLogger("apscheduler.scheduler").setLevel(logging.ERROR)` drops
47 % of the journal and costs exactly one real signal: the jobstore-read
warning, which with the in-memory jobstore in use here cannot fire. A
`logging.Filter` matching only `MaxInstancesReached` is tidier still and costs
nothing. Covered by `prompts/43`.

#### F5 — The noise is load-bearing for liveness detection. Do not remove both halves.

This is the one finding that constrains the others, and it's why `prompts/43`
deliberately does **not** turn off the access log.

Gap analysis between consecutive log lines, over the full 7-day journal:

| stream | n | p50 | p90 | p99 | max |
|---|---|---|---|---|---|
| all lines | 267,508 | 0.02 min | 0.07 min | 0.52 min | 25.1 min |
| excluding APScheduler skips | 146,465 | 0.00 min | 0.23 min | 0.53 min | 25.1 min |
| **`app.*` lines only** | **456** | **0.17 min** | **1.28 min** | **3.97 min** | **16.2 min** |

Read that bottom row carefully. **BookBrain's own application logging produces
456 lines in 7 days — 65 a day, roughly one every 22 minutes**, with a 99th
percentile gap of 4 minutes and a routine maximum of 16. On the `app.*` stream
alone, "silent for 10 minutes" is a *normal Tuesday*, and a silence alarm is
unbuildable without a huge threshold that defeats the point.

On the full stream, "no lines for 5 minutes" has a false-positive rate of
essentially zero (p99 gap = 31 seconds) and would have fired during today's
hang.

The consequence:

- Silencing the APScheduler skips (F4): **safe.** The access log remains, and
  the dashboard's own 30-second poll keeps a steady heartbeat in the stream.
- *Also* turning off the access log (which the prompt raises as an option):
  **destroys log-silence detection**, because what's left is the 456-line
  `app.*` trickle above.

So the answer to the prompt's "should uvicorn's access log be off?" is **no —
not unless something explicitly replaces it as a heartbeat.** It is currently
the only thing in the system that emits on a known, regular cadence. Its cost
is 46 % of the journal; its value is that it is BookBrain's de facto liveness
signal, and it was also (per the prompt) what confirmed the sibling passcode
login worked end-to-end. Keep it. Covered by `prompts/43` (as a non-change,
documented) and `prompts/45` (which makes the heartbeat explicit so this
coupling stops being accidental).

#### F6 — The per-provider counters in `status.json` are lifetime totals and can never show "dead now"

`status.json` **does** already carry per-provider data — `bookbrain.providers`
has an entry per provider:

```
openbooks    queued= 56  fetching=0  got= 103  failed=   7
libgen       queued=  5  fetching=0  got=  98  failed=  40
torrent      queued=  0  fetching=0  got=   4  failed=2191
```

But `got` and `failed` are **cumulative all-time counts**. A monotonically
increasing counter cannot express "this provider stopped working two hours
ago" — during the entire 18-hour OpenBooks outage in F2, `openbooks.got`
stayed pinned at its lifetime value and never decreased, because counters
don't. The panel was accurate and useless simultaneously.

The same row also shows `torrent: got=4, failed=2191` — a **0.18 % success
rate**, all-time, sitting on the dashboard as an unremarkable pair of numbers.
Whether the torrent provider is worth keeping is a separate question
(`prompts/46` raises it), but the fact that a 99.8 %-failing provider doesn't
render as a problem is the same bug as F2.

**Fix**: a windowed rate (`got`/`attempts` over the last 6 h) alongside the
lifetime totals, coloured. The `acquisition_candidates` table has
`resolved_at` and `candidate_provider`, so this is a `GROUP BY` away. Covered
by `prompts/44`.

#### F7 — The mobile dashboard computes staleness and then throws it away

`mobile/index.html`:

```js
let lastGeneratedAt = null;        // :332
...
lastGeneratedAt = d.generated_at;  // :344
```

Those are the only two occurrences in the file. **The variable is declared and
assigned, and never read.**

The failure this leaves open: `dashboard.sh` dies (or wedges on one of its
`curl`s) while `bookbrain-dashboard-mobile.service` keeps serving the last
`status.json` it wrote. The phone's `fetch` succeeds, so the catch block never
runs, so the `.stale` banner never appears — and the page renders a full set of
confident-looking green pills from an arbitrarily old file. The only hint is
the `updated 4 hours ago` subtitle (`:347`), rendered in ordinary styling at
subtitle size.

The `.stale` CSS class and the `ago()` helper both already exist. This is
maybe six lines: if `now - generated_at > 3 * refresh interval`, put the
`.stale` class on the subtitle and grey the gauges. Covered by `prompts/45`.

#### F8 — `/api/health` is a literal, so "api: ok" means only "the event loop is alive"

`app/api/routes/health.py`, in full:

```python
async def health() -> dict[str, str]:
    return {"status": "ok"}
```

It touches no database, no Drive credential, no scheduler, no provider. It is a
perfectly good *liveness* probe — and it genuinely would have caught today's
event-loop hang, because a blocked loop can't serve even a literal. But the
dashboard header renders it as `api: ok` in green, which reads as "BookBrain is
healthy", and it cannot detect: a wedged scheduler, expired Drive credentials,
a locked database (F1 — 32 occurrences this week), or any dead provider (F2).

Worth stating plainly because it's the single most prominent green thing on the
dashboard. The recommendation is **not** to build a heavyweight health
aggregator — it's to stop the word `ok` from carrying more meaning than it has,
by putting the things it doesn't cover next to it. Covered by `prompts/45`.

### P3 (noted, prompts where cheap)

#### F9 — `backend/nightly-runs.log` is dead weight and actively misleading

Confirmed: last written **2026-09-13 13:32**, 5 days stale, 66 KB. Its only
writer is `_configure_standalone_logging()` in `jobs/nightly.py:405-417`, which
runs solely under `python -m app.jobs.nightly`. There is **no systemd timer and
no crontab entry** that invokes it (`systemctl list-timers | grep -i book` and
`crontab -l` are both empty) — on this box the in-process APScheduler job is
what runs, and it logs to the journal like everything else.

The risk isn't the disk space, it's that it's a file called `nightly-runs.log`
containing a complete, plausible, well-formatted nightly summary that is five
days old. It's exactly the kind of thing a 3 a.m. investigation reads first.
Covered by `prompts/46`.

#### F10 — `status.json` is 105 KB, 40 % of it one debug field, polled twice per write

- The file has grown from the prompt's 93 KB to **105,548 bytes** during the
  audit window.
- `bookbrain.providers` alone is **42,741 bytes**, and nearly all of that is
  `searched_recent` — which carries, per provider, 5 entries each with the full
  `candidate` object *and* a `alternatives` array of full candidate objects
  (`full` handle, title, author, format, size, server, score, provider). The
  mobile page renders **three** fields from each entry: title, author, status.
- The mobile page polls every **15 s** (`setInterval(refresh, 15000)`) while
  `dashboard.sh` rewrites the file every **30 s** (`FAST_REFRESH=30`). Half of
  every phone's fetches are guaranteed-identical bytes, and `cache: 'no-store'`
  ensures none of them can be served from cache. An open phone tab pulls
  ~25 MB/hour over Tailscale, about half of it redundant.

Not growing unboundedly — `searched_recent` is sliced to 5 and the queue
fields are counts — so this is bloat, not a leak. Trimming `alternatives` out
of the sidecar and matching the poll to the write cadence is a few lines each.
Covered by `prompts/46`.

#### F11 — The hang left a distinctive, machine-readable systemd signature

Every clean restart of `bookbrain.service` today produced this sequence:

```
Stopping bookbrain.service…
uvicorn: INFO:     Shutting down
bookbrain.service: Deactivated successfully.
Stopped bookbrain.service.
```

The 09:48 restart produced this instead:

```
09:48:01  Stopping bookbrain.service…
09:49:31  Stopped bookbrain.service.
```

**No `Shutting down` from uvicorn, no `Deactivated successfully`, and 90
seconds between the two** — the signature of a process that never handled
SIGTERM because its event loop was blocked, and had to be SIGKILLed at
`TimeoutStopSec`. If a liveness check ever wants a second corroborating signal,
`Deactivated successfully` missing from a stop sequence is a clean one.

---

## 3. The incident timeline doesn't match the prompt — worth reconciling

The prompt states the backend "hung completely (~09:55–11:15)" and "stopped
logging entirely". The journal supports a hang, but a different and shorter
one, and I couldn't reproduce the 09:55–11:15 window from any source on the
box.

What the journal actually shows for today:

- `bookbrain.service` restarted at 06:27, 07:16, 07:38, 08:33, 09:24, **09:48**,
  09:53, and 11:41.
- **The only log silence is 09:33:44 → 09:48:01 (14.3 minutes)**, immediately
  preceding the anomalous hard-kill restart described in F11. That is the hang.
- Across the *entire* 7-day journal there are only two silences longer than
  this, both on 09-12 (install day, 25.1 and 22.5 min).
- From 09:53:12 onward, the log rate is a **flat 37–47 lines per minute every
  single minute through 11:44** — no gaps, no dips. Per-minute counts through
  the claimed 09:55–11:15 hang window are indistinguishable from any other
  period.
- During 10:00–11:15 specifically, `/api/viewer/*` requests were **completing
  with 200s** — 29× `GET /api/viewer/session` 200, 10× `sidecar-meta` 200, 2×
  `POST /api/viewer/session` 204, 1× `drive/blob/…` 200.
- `tailscaled` logged 14 errors in 09:30–11:30, all consistent with the
  restarts themselves (5× `connection was refused`, 5× `proxy error: context
  canceled`, 3× TLS handshake EOF) — no sustained outage at the tunnel layer.

The fix commit `2dece70` is authored **10:09:48**, which fits a 09:33–09:49
hang followed by diagnosis and a fix — and *not* an outage still in progress at
11:15. Note also that the 09:53 restart was still running the **unfixed** code
(the fix didn't reach the running service until the 11:41 restart), and it ran
for nearly two hours without incident — consistent with the root cause, which
only triggers when the stored Google token happens to be expired *and* a viewer
sync fires a burst of concurrent `/api/viewer/*` requests. Rare, not constant.

None of this changes any recommendation — a 14-minute total hang is still a
total hang, and F11's hard-kill signature confirms it. It matters for one
reason: **"the logs went silent" was true for 14 minutes, not 80**, and a
silence-based alarm needs its threshold set against that (F5's 5-minute
window), not against an 80-minute assumption that would have been comfortable
and wrong.

---

## 4. Part 3 — if BookBrain breaks at 3 a.m., what tells James?

**Nothing. Today's honest answer is that he finds out when he notices books
stopped arriving — and for the failure that actually happened this week, that
took 18 hours and he only spotted it because he happened to look.**

More precisely, by failure mode:

| failure | what would show | who'd see it at 3 a.m. |
|---|---|---|
| total hang / event loop blocked | `api: unreachable` red on tty1 + mobile; journal goes silent | nobody — tty1 is unwatched, phone is asleep |
| one provider dies | **nothing** — aggregates are covered by the others (F2) | nobody, for 18 h and counting |
| `dashboard.sh` dies | mobile shows stale data with no warning (F7) | nobody |
| DB locked / job exception | an unformatted, untimestamped traceback (F1) | nobody — `grep ERROR` finds 1 line/week |
| nightly job fails | `Last nightly job: failed` in red on tty1 | only if someone walks past the TV |
| disk fills | `Disk /` bar turns red | same |

The common thread is not that signals are missing — four of those six *do*
produce a visible signal. It's that **every signal in the system is
pull-based**: it requires a human to be looking at a screen at the moment the
thing is wrong. There is no artefact that survives until morning saying "at
03:14 this went bad."

### The smallest change that moves this meaningfully

Not a monitoring stack. Three things, in the order they pay off:

1. **Make one red thing on the dashboard mean "something is wrong right now",
   and make it cover the gaps.** A single `ALERTS` block at the top of
   `dashboard.sh`'s BookBrain section that renders one red line per active
   condition, or a green `no alerts` when there are none. The conditions worth
   having on day one, all computable from data the dashboard already fetches or
   from one `journalctl` call:
   - backend has logged nothing for > 5 minutes (F5 — threshold justified by
     the p99 = 31 s gap measurement);
   - any provider at 0 successes in 6 h while others are succeeding (F2/F6);
   - `status.json` older than 3 × `FAST_REFRESH` (F7);
   - any `Traceback` in the journal in the last hour (F1 — cheap and, once the
     root logger is fixed, precise);
   - nightly job status ≠ success.

   This is the whole recommendation. It's one panel, in the file James already
   watches, and every input already exists.

2. **Make the alert survive the night.** Append each alert transition to a
   small `dashboard/alerts.log` with a timestamp, and render the last 3 lines
   under the block. This is the part that converts "a red thing nobody was
   awake to see" into "at 03:14 OpenBooks stopped, at 07:02 it was still
   stopped" — visible at breakfast. A few lines of shell; no daemon, no
   notification service, no external dependency.

3. **Only then consider pushing.** If (1) and (2) prove the alerts are accurate
   and don't cry wolf, a `ntfy`/Pushover call on transition-to-red is a
   two-line addition. Doing it before the conditions are tuned would just train
   James to ignore his phone. Explicitly out of scope for `prompts/45`.

What this deliberately does **not** do: no Prometheus, no Grafana, no
Loki/Vector, no structured JSON logging migration, no alertmanager. For a
one-household server whose owner already has a dashboard on a TV, the bar is a
red line on that dashboard and a file that remembers. Everything above is
achievable in `dashboard.sh`, `mobile/index.html`, and ~4 lines of `main.py`.

---

## 5. Prompts created & recommended order

| # | File | Sev | One line |
|---|---|---|---|
| 43 | `prompts/43-logging-signal-to-noise.md` | **P1** | F1 configure the root logger so library errors are timestamped/levelled/greppable; F4 filter the APScheduler max-instances line (−47 % volume); F5 keep the access log, and document why |
| 44 | `prompts/44-per-provider-health.md` | **P1** | F3 `cand.server` → `cand.provider` in the acquire log lines; F2/F6 windowed per-provider success rate in `status.json` + both dashboards |
| 45 | `prompts/45-dashboard-alerts-block.md` | P2 | Part 3's alerts block + `alerts.log`; F7 wire up the dead `lastGeneratedAt` staleness; F8 stop `api: ok` overclaiming |
| 46 | `prompts/46-logging-housekeeping.md` | P3 | F9 retire `nightly-runs.log`; F10 trim `searched_recent.alternatives` + align the 15 s poll with the 30 s write; note the torrent provider's 0.18 % success rate for a decision |

**Order:**

1. **`43` first.** It's a ~10-line change that makes every subsequent
   investigation possible, and until it lands, nobody can verify their own work
   by grepping the log — including whoever does `44` and `45`. It also removes
   47 % of the journal volume, which makes reading anything easier. Do not let
   it also disable the access log (F5); the prompt says so explicitly.
2. **`44` second.** It's the fix for the failure that actually cost 18 hours
   this week, and the one-word log fix (`cand.server` → `cand.provider`) is the
   cheapest genuine win in this document. The windowed-rate half needs the
   `acquisition_candidates` `GROUP BY`, so it's more work — but it's the data
   `45`'s alert block consumes, so it has to come first.
3. **`45` third.** It depends on `44` for the per-provider input and reads
   better once `43` has made "any traceback in the last hour" a precise query.
   This is the one that actually answers "what tells James at 3 a.m."
4. **`46`** whenever. Pure housekeeping, no dependencies, safe to fold into any
   other session that's already in these files.

`43` and `46` are independent of everything and of each other. `44` → `45` is
the only hard ordering.
