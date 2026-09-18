# Task 43 — Make the log greppable: root logger + APScheduler noise filter

**Not started.** Small, backend-only, ~10 lines of real change in
`backend/app/main.py`. From `LOGGING-REVIEW-2026-09-18.md` findings F1, F4, F5.

Do this one **first** of the 43–46 batch. Until it lands, nobody — including
whoever runs `prompts/44` and `45` — can verify their own work by grepping
`journalctl`, because errors currently have no level token to grep for.

## The problem

`main.py:36-42` configures the **`app`** logger namespace (INFO, StreamHandler,
`propagate = False`). That was a real fix (commit `86de4cf`) and it's correct —
leave it alone.

But the **root logger is never configured in the server**. The only
`root.addHandler` calls in the codebase are in `jobs/nightly.py:414-417` and
`jobs/backup_job.py:117-120`, inside `_configure_standalone_logging()`, which
only runs under the standalone CLI entrypoints. So every non-`app.*` logger —
`apscheduler.*`, `sqlalchemy.*`, `websockets.*`, `httpx` — has no handler
anywhere in its chain and falls through to Python's `logging.lastResort`: a
bare `StreamHandler` at WARNING **with no formatter**.

Measured on the live box, 7 days:

| | count |
|---|---|
| `Traceback (most recent call last):` in the journal | **202** |
| lines matching `ERROR app.` | **1** |

`journalctl -u bookbrain.service | grep -i error` returns one line for the
week. The errors are all there — 32 × `sqlite3.OperationalError: database is
locked`, 5 × `Job "…" raised an exception`, 2 × `RuntimeError: Drive download
failed` — they just print like this:

```
Job "OpenBooks auto-get (trigger: interval[0:06:00], next run at: 2026-09-14 18:13:13 UTC)" raised an exception
Traceback (most recent call last):
  File "/opt/bookbrain/backend/app/services/openbooks_service.py", line 198, in _with_reconnect
```

No timestamp, no level, no logger name — so they can't be found, and can't be
correlated in time with anything else.

## Part 1 — Configure the root logger (F1)

In `main.py`, next to the existing `app`-logger block, attach **the same
formatter** to the root logger and set the root level to `WARNING`.

- WARNING, not INFO — that's the whole reason the original fix scoped itself to
  `app`. httpx logs an INFO line per HTTP request and would flood the journal.
  Verify that assumption still holds before you pick the level: check what
  `httpx` / `sqlalchemy.engine` actually emit at INFO in this app.
- `app.*` must keep behaving exactly as it does now. It has
  `propagate = False`, so it won't double-log through the new root handler —
  **confirm that** after the change by checking that a single `app.*` INFO line
  appears once, not twice, in `journalctl`.
- Reuse the existing format string
  (`"%(asctime)s %(levelname)s %(name)s: %(message)s"`) so library lines are
  visually identical to app lines.
- Update the big explanatory comment above the `app` block: it currently
  explains why the fix was scoped to `app` and not root. That reasoning is
  still valid for the *level*, but "root is never configured" will no longer be
  true. Say what the root handler is for (library WARNING+ with real
  formatting) and why it's WARNING (httpx).

Afterwards, `journalctl -u bookbrain.service | grep -E ' (ERROR|WARNING) '`
should surface the `database is locked` tracebacks with timestamps.

## Part 2 — Silence the APScheduler max-instances line (F4)

25,836 of 54,427 lines in 24 h (**47.5 %**) are:

```
Execution of job "LLM tagging (trigger: interval[0:00:02], …)" skipped: maximum number of running instances reached (1)
```

**This is not a bug and the 2s interval is not a mistake.** `scheduler.py:92-99`
documents it: tiny interval + `max_instances=1` + `coalesce` is a deliberate
"refire the instant the previous tick frees up" idiom, so the real pacing is
how long Ollama takes. Don't change `LLM_TAGGING_INTERVAL_SECONDS`. The message
is misleveled noise, not a signal.

It can be silenced surgically because APScheduler puts it on a **different
logger from job errors** (verified in the installed 3.x under
`backend/.venv/lib/python3.14/site-packages/apscheduler/`):

- `apscheduler.scheduler` (`schedulers/base.py:900-903`) logs at WARNING
  exactly two things: *"Error getting due jobs from job store"* and the
  max-instances skip. Everything serious on it is ERROR / `.exception()`.
- `apscheduler.executors.<alias>` (`executors/base.py:145,195`) is what logs
  `'Job "%s" raised an exception'`, and is unaffected by anything done to
  `apscheduler.scheduler`.

Two options — **prefer the filter**, it costs nothing and keeps the jobstore
warning:

1. A `logging.Filter` on `apscheduler.scheduler` that drops only records whose
   message contains `maximum number of running instances`.
2. `logging.getLogger("apscheduler.scheduler").setLevel(logging.ERROR)` —
   simpler, but also loses the jobstore-read warning (which, with the in-memory
   jobstore in use here, cannot fire — so this is acceptable if the filter
   turns out ugly).

Either way, **verify after the change** that a genuine job exception still
reaches the journal. The easiest honest check: find one of the existing
`Job "…" raised an exception` entries in the last 7 days and confirm your
change wouldn't have suppressed it (it's on `apscheduler.executors.default`,
not `apscheduler.scheduler`).

## Part 3 — Do NOT turn off the uvicorn access log (F5)

The audit raised this and the answer came back **no**. Record the reasoning in
a comment near the logging config so the next person doesn't re-litigate it.

Access lines are 45.6 % of the journal, and the obvious move is to kill them
too. Don't. Gap analysis over the 7-day journal:

| stream | n | p50 | p99 | max |
|---|---|---|---|---|
| all lines | 267,508 | 0.02 min | 0.52 min | 25.1 min |
| **`app.*` lines only** | **456** | **0.17 min** | **3.97 min** | **16.2 min** |

BookBrain's own application logging is **456 lines in 7 days** — one every
~22 minutes, with a routine 16-minute gap. On that stream alone, "silent for
10 minutes" is normal and a log-silence alarm is unbuildable.

With the access log on, "no lines for 5 minutes" has a near-zero false-positive
rate (p99 gap 31 s) and would have caught today's 14-minute hang. The
dashboard's own 30s poll is currently BookBrain's **de facto heartbeat**, and
`prompts/45` builds a silence alarm on top of it. The access log also
(per the original audit prompt) confirmed the sibling passcode login worked
end-to-end.

Removing Part 2's noise is safe precisely because the access log stays.

## Constraints

- This box **is** the production server (`/opt/bookbrain` on `homeserver`).
  Changes are live for the family viewer.
- Restarting `bookbrain.service` to pick up the change is expected and fine
  here (unlike the read-only audit) — but do it once, deliberately, and confirm
  `Application startup complete` plus a clean `Deactivated successfully` in the
  stop sequence afterwards.
- Run the backend tests before committing.
- Expected result: journal volume drops from ~54 k to ~28 k lines/day, and
  `grep -E ' (ERROR|WARNING) '` becomes a useful first move.
