# Task 42 — Logging + dashboard audit: does BookBrain actually tell you when it breaks?

**Not started.** Run in its own fresh Claude Code session. This is a
**read-only audit** that produces a written assessment plus follow-up numbered
prompts. Don't fix things as you find them — a couple of the likely fixes
(log-level changes, adding a health signal) are small enough to be tempting,
but batching them into their own prompts keeps this session's findings
reviewable and lets James pick what's worth doing.

## Why now

On 2026-09-18 two real failures went undetected for hours, and in both cases
the information needed to spot them existed but wasn't surfaced anywhere
James looks:

1. **The backend hung completely** (~09:55–11:15). `systemctl` still reported
   `active`, the process was alive and accepting TCP connections, but every
   request hung forever — hundreds of sockets piled up in `CLOSE-WAIT` on
   `:8000` and the service stopped logging entirely. Root cause was a
   synchronous Google token refresh blocking the asyncio event loop (fixed,
   commit `2dece70`). **The only reliable signal was "the logs went silent"**
   — which nothing watches for.
2. **OpenBooks silently stopped returning any search results** for ~1h40m
   (and intermittently for two days before that). Each failure *was* logged
   as a `WARNING`, but as one line among tens of thousands, with no
   aggregation, so a 15-in-a-row total failure looked identical to routine
   noise. Root cause was an IRC JOIN race (fixed, commit `baa1af2`).

James's summary of the second one was "this seems to be a recurring problem"
— which is the real point of this audit. The failures get found by James
noticing books stopped arriving, not by the system saying anything.

## What exists today (verify all of this — it's a sketch, not gospel)

**Logging**
- `backend/app/main.py` configures the `app` logger namespace explicitly
  (INFO, `StreamHandler`, `propagate = False`) because uvicorn never touches
  the root logger. Read the comment there — it documents a real past bug where
  every `app.*` INFO line was silently dropped.
- Everything goes to stdout → `journalctl -u bookbrain.service`. No file, no
  rotation config of BookBrain's own, no structure (plain text, not JSON).
- `backend/nightly-runs.log` is written by the **standalone** nightly runner
  (`python -m app.jobs.nightly`), which on this Linux box isn't the layer that
  actually runs — the in-process APScheduler one is. Confirm whether that file
  is stale/dead weight (it was last written 2026-09-13).
- `journalctl --disk-usage` was **170 MB** at audit time.

**Two different "dashboards" — don't conflate them**
- **Server dashboard** (`dashboard/dashboard.sh`, 636 lines, runs on tty1 via
  `bookbrain-dashboard.service`): an infinite redraw loop, `FAST_REFRESH=30`s,
  with a slower tier for expensive calls. It writes
  `dashboard/mobile/status.json` (93 KB at audit time), which
  `dashboard/mobile/index.html` polls — that's the phone-friendly view, served
  over Tailscale (tailnet-only, port 443; see the infra note in memory).
- **Viewer Dashboard** (`library-viewer/src/components/DashboardScreen.tsx` ←
  `lib/dashboard.ts` ← `bookbrain-dashboard.json` on Drive, written by
  `library_index_service.regenerate_dashboard`): the family-facing acquisition
  snapshot, refreshed by the nightly job and after each auto-get.

If James only meant one of these, it's almost certainly the **server**
dashboard (the operational one he actually watches) — but cover both, because
the second has the same "is it telling the truth?" question and is cheap to
include once you're in there.

## Part 1 — What's actually in the logs

Measured over 24h at audit time (re-measure; these are the numbers to beat):

| | lines | share |
|---|---|---|
| total `bookbrain.service` lines | 54,437 | 100% |
| `LLM tagging ... skipped: maximum number of running instances reached (1)` | 25,840 | **47%** |
| HTTP access lines (`"GET /api/..."`) | 24,822 | **46%** |
| everything else (i.e. all actual signal) | ~3,775 | **7%** |

So ~93% of the log volume is noise, and **the dashboard's own 30s polling
generates about half of it** — the monitoring is the biggest single writer to
the thing you'd read to diagnose a problem.

Work out:
- Whether the LLM-tagging line is a *bug* or just misleveled. It fires every
  2s forever whenever a tagging run is in flight (the job's interval is 2s and
  APScheduler refuses overlapping instances). Is a 2s interval for a job that
  takes minutes the actual intent? If it is, that message should be DEBUG or
  suppressed — it's APScheduler's own logger, so check whether it can be
  quieted without losing genuine scheduler errors.
- Whether uvicorn's access log should be off (or filtered to non-2xx) given
  that a health-poll every 30s dominates it. Note the trade-off: the
  `/api/viewer/*` access lines were genuinely useful today for confirming the
  sibling passcode login worked end-to-end.
- Whether the remaining 7% is the *right* 7%: is there a line for every
  outcome that matters (auto-get success/failure per provider, nightly
  start/finish/failure, Drive auth refresh/expiry, job exceptions), and is
  each at a level that matches its severity?
- Retention: 170 MB of journal with no BookBrain-side policy. What's the
  effective window, and is it long enough to investigate a "recurring"
  problem like the OpenBooks one (which needed 7 days of history to see the
  pattern)?
- One known cosmetic bug to confirm: `acquire: auto-got ... via None` — for
  non-OpenBooks providers `cand.server` is `None`, so the log line never names
  the provider that actually supplied the book. Made today's diagnosis harder
  (it looked like OpenBooks was working when Libgen was). See
  `acquisition_service.py` ~line 1466.

## Part 2 — What the dashboards actually report

For the **server dashboard**, map every panel back to its source. It polls
the API (`/api/health`, `/api/files?status=inbox`, `/api/reviews`,
`/api/duplicates`, `/api/local-scan/pending`, `/api/jobs/nightly`,
`/api/library/recently-organized`, `/api/acquire/requests`,
`/api/library/llm-tagging`) plus `df` and `systemctl is-active`.

The key question: **it polls for state, but never reads the logs** — so
anything that only manifests as log output is invisible to it by
construction. Check specifically:
- What it showed during today's hang. `systemctl is-active` said `active`
  while every request hung, and the health call is `curl -m 2` — so did the
  panel show `RUNNING` next to an unreachable health check, and would that
  read as "fine" at a glance? Is there any "backend has been silent for N
  minutes" concept anywhere?
- Whether a totally dead acquisition provider is distinguishable from a quiet
  one. OpenBooks returned zero results for 1h40m while Libgen kept working —
  did any panel move?
- `status.json` is 93 KB and rewritten every 30s. Check what's in it, whether
  it's growing unboundedly, and whether the mobile page's no-store fetch of
  it on an interval is sane.
- Error handling: several `curl`s fall back to `"?"` on failure. How many
  panels can be simultaneously `?` before the dashboard is lying by omission?

For the **viewer Dashboard**, confirm `bookbrain-dashboard.json`'s freshness
story: it's written by the nightly job and after each auto-get, so a backend
that's been down for hours still serves a stale-but-plausible snapshot to the
family viewer. Is a generated-at timestamp shown anywhere in the UI?

## Part 3 — The gap, stated plainly

Finish with a short, direct answer to: **if BookBrain breaks at 3am, what
tells James, and how long does it take?** Today's honest answer is "nothing;
he notices books stopped arriving days later." Recommend the smallest change
that moves that meaningfully — a heartbeat/liveness check, a per-provider
success-rate panel, a log-silence alarm, whatever the evidence supports. Don't
propose a monitoring stack; this is a one-household server and the bar is "a
red thing on the dashboard James already looks at."

## Deliverable

- `LOGGING-REVIEW-<date>.md` in the repo root, following the shape of the
  existing `REVIEW-*.md` files: findings numbered and severity-tagged, each
  with the evidence that supports it (real log excerpts, real counts) and a
  concrete recommendation.
- Follow-up numbered prompts in `prompts/` for anything worth doing, plus the
  `prompts/README.md` table entry for each, exactly as the existing review
  batches do.
- Be blunt about anything that turns out to be fine. A short "these are
  already correct" list is a useful result and stops the next session
  re-investigating it.

## Constraints

- **Read-only.** No code changes, no config changes, no restarting
  `bookbrain.service` or `bookbrain-dashboard.service` to "see what happens" —
  the backend is live and the family viewer depends on it.
- Reading `journalctl`, the log files, `status.json`, and the source is all
  fair game. So is inspecting a running process (`ss`, `ps`, `systemctl
  status`).
- Don't touch `dashboard.sh`'s output files; `status.json` is rewritten by a
  live loop.
- This box **is** the production server (`/opt/bookbrain` on `homeserver`) —
  not a checkout. Anything you run, runs for real.
