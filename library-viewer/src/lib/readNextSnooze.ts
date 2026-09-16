// bookbrain-readnext-snoozed.json — "Read next" cards the reader has X'd away.
// Unlike a news dismissal these come BACK: a snooze lasts SNOOZE_DAYS, then the
// card returns (if still unread — the strip only ever lists unread books).
//
// Drive sidecar = source of truth (syncs across devices); localStorage is a
// cache for instant first render / offline. Goes through `makeSidecar` for the
// serialised write + concurrency guard (prompts/34).

import { makeSidecar } from './syncedSidecar'

const FILENAME = 'bookbrain-readnext-snoozed.json'
export const SNOOZE_DAYS = 30
const SNOOZE_MS = SNOOZE_DAYS * 24 * 60 * 60 * 1000

// driveFileId -> epoch ms it was snoozed
export type SnoozeMap = Record<string, number>

function parse(raw: unknown): SnoozeMap {
  const snoozed = (raw as { snoozed?: unknown } | null | undefined)?.snoozed ?? raw
  const out: SnoozeMap = {}
  if (snoozed && typeof snoozed === 'object') {
    for (const [id, at] of Object.entries(snoozed as Record<string, unknown>)) {
      if (typeof at === 'number' && Number.isFinite(at)) out[id] = at
    }
  }
  return out
}

// Drop entries older than SNOOZE_DAYS — those cards have "come back".
function fresh(map: SnoozeMap, now = Date.now()): SnoozeMap {
  const out: SnoozeMap = {}
  for (const [id, at] of Object.entries(map)) {
    if (now - at < SNOOZE_MS) out[id] = at
  }
  return out
}

// A join: keep the latest snooze per card, then prune expired ones.
function merge(a: SnoozeMap, b: SnoozeMap): SnoozeMap {
  const out: SnoozeMap = { ...a }
  for (const [id, at] of Object.entries(b)) {
    if (out[id] == null || at > out[id]) out[id] = at
  }
  return fresh(out)
}

const sidecar = makeSidecar<SnoozeMap>({
  filename: FILENAME,
  cacheKey: 'bookbrain.readNextSnoozed',
  empty: {},
  parse,
  serialise: (map) => ({ version: 1, snoozed: fresh(map) }),
  merge,
})

// Which drive file ids are still snoozed right now.
export function activeSnoozes(map: SnoozeMap, now = Date.now()): Set<string> {
  return new Set(Object.keys(fresh(map, now)))
}

// Synchronous — the cached map, for the first render before Drive answers.
export function cachedReadNextSnoozes(): SnoozeMap {
  return fresh(sidecar.cachedNow())
}

// Read the sidecar, fold the local cache in, drop expired entries, seed the
// sidecar if the cache held more.
export async function readNextSnoozesSynced(
  token: string,
  libraryFolderId: string,
): Promise<SnoozeMap> {
  return fresh(await sidecar.sync(token, libraryFolderId))
}

// Snooze one card. Serialised read-modify-write with the concurrency guard.
export async function snoozeReadNext(
  token: string,
  libraryFolderId: string,
  driveFileId: string,
  _current: SnoozeMap,
): Promise<SnoozeMap> {
  return sidecar.write(token, libraryFolderId, (map) =>
    fresh({ ...map, [driveFileId]: Date.now() }),
  )
}
