# Task 34 — one `syncedSidecar` helper + Drive `If-Match` concurrency (REVIEW-2026-09-10 F6/F3)

Read `prompts/README.md`. `library-viewer`-only; ships on push to `main`.
Mostly structural — the one real behaviour change is F3 (optimistic
concurrency). No backend change.

---

## Why

### F6 — 11 hand-rolled sidecar modules

`library-viewer/src/lib/`: `libraryIndex.ts`, `recommendations.ts`,
`newReleases.ts`, `reading.ts`, `news.ts`, `prompts.ts`, `lists.ts`,
`readNextSnooze.ts`, `wishlist.ts`, `activityLog.ts`, `readingQueue.ts` — each
re-implements the same ~40 lines:

```ts
const query = encodeURIComponent(`'${folderId}' in parents and name = '${FILE}' and trashed = false`)
const listResp = await fetch(`…/files?q=${query}&fields=files(id,modifiedTime)&pageSize=1`, { headers })
if (files.length === 0) return cacheValid ? cached.X : EMPTY
const { id, modifiedTime } = files[0]
if (cacheValid && cached.modifiedTime === modifiedTime) return cached.X
const fileResp = await fetch(`…/files/${id}?alt=media`, { headers })
const X = normaliseX(await fileResp.json())
localStorage.setItem(CACHE_KEY, JSON.stringify({ folderId, modifiedTime, X }))
return X
```

`news.ts` and `readNextSnooze.ts` on top of that each hand-roll `cachedX()` /
`XSynced()` (read → merge cache → seed) / a module `writeChain` / `pruneX()`.
It's not a bug today; it's where the next one gets seeded, and F3's fix would
otherwise have to be pasted into every RMW caller.

### F3 — RMW sidecar writes have no concurrency control

`drive.ts:writeJsonFile` is an unconditional full-file `PATCH …?uploadType=media`.
`queueReadingChange` / `dismissNewsItem` / `snoozeReadNext` / `addToWishlist`
read → merge → write with **no lock and no `If-Match`**. Two devices inside
the read→write window → last write wins on the whole file.

`bookbrain-wishlist.json` / `bookbrain-activity-log.json` accept this (a lost
line doesn't matter). **`bookbrain-reading-pending.json` does not** — it's a
sync *queue*; a clobbered entry is a "mark read" that never reaches Hardcover
and James can't see it. This session added `news-dismissed` +
`readnext-snoozed` in the same shape.

Drive supports optimistic concurrency: send `If-Match: <etag>` on the `PATCH`;
a mismatch returns `412`. That's the fix, and it belongs in the shared helper.

---

## Goal

### 1. `lib/syncedSidecar.ts` — one primitive

```ts
export interface SidecarSpec<T> {
  filename: string        // e.g. 'bookbrain-news.json'
  cacheKey: string        // e.g. 'bookbrain.news'
  empty: T
  parse: (raw: unknown) => T          // == the existing normaliseX
  // for read-modify-write sidecars only:
  merge?: (local: T, remote: T) => T  // combine a local change with the server copy
}

export function makeSidecar<T>(spec: SidecarSpec<T>): {
  cachedNow(): T                                   // sync, from localStorage (useState init)
  fetch(token: string, folderId: string): Promise<T>       // modifiedTime-gated read + cache
  sync(token: string, folderId: string): Promise<T>        // read + merge(cache) + seed, for RMW
  write(token: string, folderId: string,
        update: (current: T) => T): Promise<T>              // RMW with If-Match retry, serialised
}
```

- `fetch` = the ~40-line dance, once. modifiedTime cache in
  `{ folderId, modifiedTime, value }`.
- `write`:
  - `readJsonFile` (now returning `etag` too — see step 2) → apply `update`
    → `writeJsonFile(…, etag)` → on `412`: re-read, re-apply `update`, retry
    (bounded, ~3).
  - serialised through a per-`filename` promise chain (what `news.ts` does now
    for one file, generalised).
  - updates the localStorage cache with the written value + new modifiedTime.
- `sync` = `fetch` but merges `cachedNow()` in via `spec.merge` and seeds the
  sidecar when the cache had more (the current `dismissedNewsSynced` /
  `readNextSnoozesSynced` behaviour), using `write` so it gets `If-Match` too.

### 2. `drive.ts`

- `readJsonFile` return type gains `etag: string` (Drive's `files.get` /
  `files.list` returns `etag` in `fields`; add it to the `fields=` param).
- `writeJsonFile` gains an optional `ifMatch?: string`; when set, add the
  `If-Match` header to the `PATCH`; on `resp.status === 412` throw a
  distinguishable `SidecarConflictError` the helper catches.

### 3. Migrate

- **Read-only first** (mechanical, no behaviour change): `newReleases.ts`,
  `reading.ts`, `prompts.ts`, `lists.ts`, `recommendations.ts`,
  `news.ts:fetchNews`. Each becomes ~5 lines
  (`const sidecar = makeSidecar({ … }); export const fetchNews = sidecar.fetch`).
  Keep the exported `normaliseX` functions (they're `spec.parse` and have
  their own tests).
- `libraryIndex.ts` carries extra shape (`coversFolder`, `series`) — either
  fit it to `SidecarSpec` or leave it; it's not RMW and not the point.
- **RMW**: `readingQueue.ts` (`queueReadingChange` → `sidecar.write`,
  `loadPendingReading` → `sidecar.fetch` + a selector), `news.ts` dismiss
  trio, `readNextSnooze.ts`, `wishlist.ts` (`addToWishlist` /
  `saveWishlist` / `reconcile`), `activityLog.ts` (`logActivity`). Each loses
  its bespoke `writeChain` / cache read/write.
- Delete the now-dead helpers.

---

## Acceptance criteria

- `syncedSidecar.ts` exists; `readJsonFile` returns `etag`; `writeJsonFile`
  honours `If-Match` and a 412 triggers a bounded re-read/re-merge/retry.
- Every sidecar module is either migrated or explicitly left (with a one-line
  reason). Net ~300–500 lines removed.
- Behaviour is unchanged for read-only sidecars; RMW sidecars now survive a
  concurrent write instead of clobbering.
- `cd library-viewer && npm test && npm run build && npm run lint` green. Add
  a `syncedSidecar.test.ts`: a 412 on the first write → re-read → merge →
  second write succeeds; the serialised chain applies two `write`s in order.
- The existing `news.test.ts` / `readNextSnooze.test.ts` / `wishlist.test.ts`
  still pass (adjust mocks to the new `etag` field, keep the assertions).
- One commit (or two: helper+drive, then migration). Update `prompts/README.md`,
  `REVIEW-2026-09-10.md` (mark F3/F6 done), and the relevant memories
  (`project-bookbrain-sff-news`, `project-bookbrain-reading-status`,
  `project-bookbrain-wishlist`).

## Gotchas

- The viewer test env is `node` with a `localStorage` shim
  (`src/test/setup.ts`) — the helper's cache reads/writes work there.
- `writeJsonFile`'s *create* path (no existing file) has no etag — only the
  *update* path gets `If-Match`. A create racing a create is still
  last-wins, but that's a one-time event per sidecar.
- Don't change any sidecar's on-disk JSON shape — the backend writes several
  of these and the viewer must stay compatible both ways.
- `koboDeviceSync.ts` also does RMW on `bookbrain-viewer-settings.json` — fold
  it in too, or note why not.
