# Task 44 — Per-provider acquisition health: name the source, measure it in a window

**Not started.** Backend + `dashboard.sh`. From `LOGGING-REVIEW-2026-09-18.md`
findings F3, F2, F6.

Run **after `prompts/43`** (so you can verify your own work by grepping the
journal) and **before `prompts/45`** (which consumes the per-provider signal
this task produces).

## Why this one matters most

On 2026-09-17 at ~18:00 OpenBooks stopped returning results and stayed broken
for **18 hours**, until `baa1af2` was deployed at 11:41 on 09-18. Not the
"~1h40m" it felt like — a continuous block of 7–8 failures/hour at the job's
full tick rate, every hour, all night.

Nothing anywhere moved, because Libgen absorbed the load. Successful downloads
per 6 h bucket from `acquisition_candidates`:

```
bucket            openbooks   libgen  torrent
2026-09-17 06h            0        4        0
2026-09-17 12h            0        7        1
2026-09-17 18h            1       40        0    <- openbooks dead, best bucket on record
2026-09-18 00h            1        7        0
```

Every aggregate on the dashboard — `organized last 24h`, `organized last 7d`,
the sparkline, `hit rate`, `Last book downloaded via auto-get` — was at or
above normal throughout. There was no panel that *could* have moved. That's the
gap this task closes.

## Part 1 — `cand.server` → `cand.provider` (F3, five minutes, do it first)

`acquisition_service.py:1466`:

```python
logger.info("acquire: auto-got %r (%s) via %s", title, result.get("filename"), cand.server)
```

`cand` is an `AcquisitionResult`. Its `server` field is **OpenBooks-specific**
(the IRC bot nick) and is `None` for every other provider. The field that names
the provider is on the same dataclass (`providers/acquisition/types.py`):

```python
server: str | None
...
provider: str = "openbooks"   # <- this one
```

Live evidence: of 82 `auto-got` lines in 24 h, **81 say `via None`**; exactly
one named a source (`via Ook`). Across the journal, `via None` appears 94 times.

**Trap:** the local variable `provider` in scope at line 1466 is the
**`DriveProvider`** (the upload destination), not the acquisition source. The
correct expression is `cand.provider`.

Fix all four sites:
- `:1466` the `auto-got` info line
- `:1473` the returned `{"server": cand.server}` dict
- `:1475` the `auto-get failed … via %s` warning
- `:1477` the returned `{"failed": …, "server": cand.server}` dict

For the two return dicts, decide deliberately whether to *add* a `provider` key
or rename `server` — check every consumer first (`dashboard.sh` reads these via
`/api/acquire/requests`, and `mobile/index.html` renders them). Keeping
`server` and adding `provider` is the safer call; say which you chose and why.

Keep the IRC bot nick in the log line where it exists — `via libgen` is right
for Libgen, and `via openbooks/Oatmeal` is strictly more useful than either
`via None` or a bare `via openbooks`. Suggested: `via %s` with
`cand.provider + (f"/{cand.server}" if cand.server else "")`.

This is a one-word class of fix that was directly responsible for the 18-hour
outage being un-measurable from the log. Verify afterwards that new `auto-got`
lines name a real provider.

## Part 2 — A windowed per-provider success rate (F6)

`status.json` already has a `bookbrain.providers` block, one entry per provider:

```
openbooks    queued= 56  fetching=0  got= 103  failed=   7
libgen       queued=  5  fetching=0  got=  98  failed=  40
torrent      queued=  0  fetching=0  got=   4  failed=2191
```

`got` and `failed` are **cumulative all-time counts**. A monotonic counter can
never express "this stopped working two hours ago" — during the entire 18-hour
outage, `openbooks.got` sat pinned at its lifetime value, because counters
don't go down.

(Note the same block shows `torrent: got=4, failed=2191` — a **0.18 %** all-time
success rate rendering as an unremarkable pair of numbers. `prompts/46` raises
whether that provider earns its keep; don't decide it here, but don't let the
new panel hide it either.)

Add a **windowed** rate alongside the lifetime totals. The data is already in
SQLite — `acquisition_candidates` has `candidate_provider`, `status`, and
`resolved_at`, so this is one `GROUP BY`:

```sql
SELECT candidate_provider, status, COUNT(*)
FROM acquisition_candidates
WHERE resolved_at >= datetime('now', '-6 hours')
GROUP BY 1, 2
```

Decisions to make (state your reasoning):
- **Window length.** 6 h is the audit's suggestion — long enough that a quiet
  provider with a small queue isn't flagged, short enough to catch an overnight
  death by morning. Check it against real data before committing to it.
- **Where it's computed.** Preferably a backend endpoint (or an addition to an
  existing one) rather than more `jq` in `dashboard.sh` — the shell script is
  already 636 lines and the viewer Dashboard could use the same numbers.
- **What "dead" means.** The useful condition is *0 successes in the window
  while at least one other provider is succeeding* — that's what distinguishes
  a dead provider from a quiet night. A provider with an empty queue must not
  be flagged.

## Part 3 — Surface it

- **Server dashboard** (`dashboard/dashboard.sh`): the "Acquisition processes"
  section already draws one box per source. Add the windowed rate to each box
  (`6h: 0/47` etc.) and colour it — red on the dead-provider condition above.
  This is the panel that should have moved on 09-17.
- **Mobile** (`dashboard/mobile/index.html`): `renderProviderCard` already
  renders per-provider counts; add the windowed figure there. There's an
  existing `pill-red` class and a `setPill` helper to reuse.
- **Viewer Dashboard** — optional and lower value. It's the family-facing
  acquisition snapshot; "OpenBooks is down" is James's problem, not the
  family's. Skip unless it falls out for free.

Leave the actual *alerting* to `prompts/45` — this task produces the signal and
shows it; `45` turns it into a red line at the top of the dashboard and a
timestamped entry in `alerts.log`.

## Constraints

- Production box. The family viewer depends on `bookbrain.service`.
- `dashboard.sh` runs in a live redraw loop writing `status.json` every 30 s —
  edit the script, but don't hand-edit its output files.
- Backend tests before committing.
- Verify Part 1 against the live journal (new `auto-got` lines naming a real
  provider) and Part 2 against the numbers in
  `LOGGING-REVIEW-2026-09-18.md` §F2 — the 6 h buckets there are a known-good
  fixture to check your `GROUP BY` against.
