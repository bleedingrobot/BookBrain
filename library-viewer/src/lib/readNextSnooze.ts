// bookbrain-readnext-snoozed.json — "Read next" cards the reader has X'd away.
// Unlike a news dismissal these come BACK: a snooze lasts SNOOZE_DAYS, then the
// card returns (if still unread — the strip only ever lists unread books).
//
// Drive sidecar = source of truth (syncs across devices); localStorage is a
// cache for instant first render / offline. Same shape + write-serialisation
// as the news-dismiss code in news.ts.

import { readJsonFile, writeJsonFile } from './drive'

const FILENAME = 'bookbrain-readnext-snoozed.json'
const CACHE_KEY = 'bookbrain.readNextSnoozed'
export const SNOOZE_DAYS = 30
const SNOOZE_MS = SNOOZE_DAYS * 24 * 60 * 60 * 1000

// driveFileId -> epoch ms it was snoozed
export type SnoozeMap = Record<string, number>

interface SnoozeFile {
  version?: number
  snoozed?: unknown
}

function parse(raw: unknown): SnoozeMap {
  const out: SnoozeMap = {}
  if (raw && typeof raw === 'object') {
    for (const [id, at] of Object.entries(raw as Record<string, unknown>)) {
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

function readCache(): SnoozeMap {
  try {
    const raw = localStorage.getItem(CACHE_KEY)
    return raw ? parse(JSON.parse(raw)) : {}
  } catch {
    return {}
  }
}

function writeCache(map: SnoozeMap): void {
  try {
    localStorage.setItem(CACHE_KEY, JSON.stringify(map))
  } catch {
    /* private mode / over quota */
  }
}

function merge(a: SnoozeMap, b: SnoozeMap): SnoozeMap {
  const out: SnoozeMap = { ...a }
  for (const [id, at] of Object.entries(b)) {
    if (out[id] == null || at > out[id]) out[id] = at // latest snooze wins
  }
  return out
}

// Which drive file ids are still snoozed right now.
export function activeSnoozes(map: SnoozeMap, now = Date.now()): Set<string> {
  return new Set(Object.keys(fresh(map, now)))
}

// Synchronous — the cached map, for the first render before Drive answers.
export function cachedReadNextSnoozes(): SnoozeMap {
  return fresh(readCache())
}

// Read the sidecar, merge the local cache in (so an older cache-only install
// seeds it), drop expired entries, and — if anything changed — write it back.
export async function readNextSnoozesSynced(
  token: string,
  libraryFolderId: string,
): Promise<SnoozeMap> {
  const cached = readCache()
  try {
    const found = await readJsonFile<SnoozeFile>(token, libraryFolderId, FILENAME)
    const server = parse(found?.content?.snoozed)
    const next = fresh(merge(server, cached))
    writeCache(next)
    if (JSON.stringify(next) !== JSON.stringify(server)) {
      try {
        await writeJsonFile(
          token,
          libraryFolderId,
          FILENAME,
          { version: 1, snoozed: next },
          found?.id ?? null,
        )
      } catch {
        /* best-effort */
      }
    }
    return next
  } catch {
    return fresh(cached)
  }
}

let writeChain: Promise<unknown> = Promise.resolve()

// Snooze one card. Read → merge with the server copy + the cache → write.
// Serialised so a burst of X's doesn't clobber.
export function snoozeReadNext(
  token: string,
  libraryFolderId: string,
  driveFileId: string,
  current: SnoozeMap,
): Promise<SnoozeMap> {
  const optimistic = fresh({ ...current, [driveFileId]: Date.now() })
  writeCache(optimistic)
  const run = writeChain.then(async () => {
    try {
      const found = await readJsonFile<SnoozeFile>(token, libraryFolderId, FILENAME)
      const merged = fresh(merge(merge(parse(found?.content?.snoozed), readCache()), optimistic))
      await writeJsonFile(
        token,
        libraryFolderId,
        FILENAME,
        { version: 1, snoozed: merged },
        found?.id ?? null,
      )
      writeCache(merged)
      return merged
    } catch {
      return optimistic
    }
  })
  writeChain = run.catch(() => undefined)
  return run
}
