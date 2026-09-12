# Task 35 — admin "Discovery data" refresh panel (REVIEW-2026-09-10 F5)

**SHIPPED 2026-09-10.** `GET /api/library/discovery-status` →
`library_index_service.discovery_status` (one folder listing + a download per
present sidecar; `{generatedAt, version?, count?}` or `null`, embeddings.bin
header parsed, corrupt file → `null`). `frontend/src/components/DiscoveryPanel.tsx`
in Settings: one row per sidecar (index / reading / recs / new-releases /
prompts / lists / news / embeddings) with "Nh ago" (red past 36h) + a
fire-and-forget **Refresh**, plus one "Full Hardcover re-sync (slow)" button
that fires `series-catalog`, `book-recs`, `new-releases/refresh` (`?stale_days=0`)
+ `embeddings/refresh` with `Promise.allSettled` and treats a timeout as
"still running". `discovery_status` unit-tested (shape + missing + corrupt +
newReleases count sum).


Read `prompts/README.md`. Backend gets one small read-only endpoint; the rest
is `frontend/` (the local admin app). No Anthropic, no real-Drive write in
tests.

## Why

`frontend/src/services/api.ts:207-236` exposes `refreshLibraryIndex`,
`generateCovers`, `backfillDescriptions`, `writeEmbeddedMetadata` — but
**nothing** for the Hardcover / discovery sidecars:

- `POST /api/library/book-recs/refresh` (+ `?stale_days=`)
- `POST /api/library/series-catalog/refresh` (+ `?stale_days=`)
- `POST /api/library/new-releases/refresh` (+ `?stale_days=`) and
  `POST /api/library/new-releases`
- `POST /api/library/reading`
- `POST /api/library/prompts`
- `POST /api/library/lists`
- `POST /api/library/news`
- `POST /api/library/embeddings/refresh` and `POST /api/library/embeddings`

Those routes exist and get run by hand (`curl`) whenever a sidecar goes wrong
— which happened repeatedly in the 2026-09-10 session (rate-limit truncation,
the narrator-name match bug, the feed retune). James is **not terminal-savvy**
(memory `user-cli-comfort`) — when a sidecar is stale or wrong his only option
today is to wait for a nightly that might hit the same bug.

## Goal

### Backend — one endpoint

`GET /api/library/discovery-status` → for each sidecar, its `generatedAt`
(and `version` where present) read from the Drive folder:

```json
{
  "index":        { "generatedAt": "2026-09-10T00:11:…", "version": 7, "count": 2470 },
  "recommendations": { "generatedAt": "…", "count": 963 },
  "newReleases":  { "generatedAt": "…", "version": 2 },
  "reading":      { "generatedAt": "…", "version": 4, "count": 405, "partial": false },
  "prompts":      { "generatedAt": "…", "count": 40 },
  "lists":        { "generatedAt": "…", "count": 72 },
  "news":         { "generatedAt": "…", "count": 50 },
  "embeddings":   { "generatedAt": "…", "count": … }   // from the .bin header if cheap, else omit
}
```

- Reuse `library_index_service._read_json_file` (added `c9516e9`). One
  `provider.list_files_in_folder` call, then a `download_file` per sidecar
  that exists — bounded, no Hardcover calls. Best-effort per file (missing →
  omitted or `null`).
- Needs creds + a library folder, same guard as the other library routes.

### Frontend — a "Discovery data" card in Settings

`frontend/src/pages/Settings.tsx` (or a new `components/DiscoveryPanel.tsx`):

- One row per sidecar: name · "generated 6h ago" (relative, red if > 36h) ·
  a **Refresh** button.
- Refresh buttons POST the matching route. The recs / series / new-releases /
  embeddings ones are long-running and some already return a job status or
  just `{...counts}` — for the fire-and-forget ones (`/reading`, `/prompts`,
  `/lists`, `/news`, `/new-releases`) show a spinner until the POST resolves
  then re-fetch `discovery-status`. For `book-recs/refresh` /
  `series-catalog/refresh` / `embeddings/refresh` (which page through the
  library and may need looping / take minutes) — a single click with
  `?stale_days=0` is the "force a full re-sync" button; label it as such and
  don't try to loop it in the UI (the nightly + a repeat click cover
  convergence).
- New `api.ts` methods mirroring the routes; new types in `frontend/src/types/`.
- Wire it into TanStack Query like the rest of Settings (a `discoveryStatus`
  query, invalidated on any refresh mutation success).

## Acceptance criteria

- `GET /api/library/discovery-status` returns the per-sidecar
  `generatedAt` / `version` / `count`; a missing sidecar is handled.
- Settings shows the panel; each Refresh button hits its route and the
  timestamps update after.
- Backend test for the new endpoint (mock the provider, assert the shape,
  assert a missing file is tolerated).
- `cd backend && python -m pytest -q` green; `cd frontend && npm run build &&
  npm run lint` green.
- One commit. Update `prompts/README.md`, `REVIEW-2026-09-10.md` (F5 done),
  and `SPEC.md` §7/§8 if you want (the admin surface grew).

## Gotchas

- **Read-only endpoint** — it must never trigger a regeneration, just report.
- Don't add the discovery panel to the *library-viewer* (family app) — it's
  an operator tool, admin-only.
- `embeddings.bin` isn't JSON; either parse its small header for a count or
  just omit it from the status (still give it a Refresh button).
- The long-running refreshes can exceed a browser fetch timeout while the
  server keeps working — the UI should treat a timeout as "probably still
  running, re-check status shortly", not "failed".
