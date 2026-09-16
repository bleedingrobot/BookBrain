// Downloaded EPUB bytes, cached in IndexedDB so a book opens instantly the
// second time and works with the network off (the app is a PWA). Bounded:
// evict the least-recently-opened books once the cache passes either limit.
//
// The IndexedDB glue is thin and manual-QA only (the vitest env is `node`,
// no indexedDB); the eviction decision is a pure function, unit-tested.

const DB_NAME = 'bookbrain-books'
const STORE = 'epubs'
const DB_VERSION = 1

export const MAX_BYTES = 300 * 1024 * 1024 // ~300 MB
export const MAX_COUNT = 20

export interface CacheEntryMeta {
  fileId: string
  bytes: number
  lastUsedAt: number
}

interface CacheRecord extends CacheEntryMeta {
  blob: Blob
  savedAt: number
}

/**
 * Which fileIds to drop so the cache fits both limits, least-recently-used
 * first. `incoming` (optional) is the fileId about to be written — it's never
 * chosen for eviction and its bytes count toward the total.
 */
export function evictionPlan(
  entries: CacheEntryMeta[],
  opts: { maxBytes?: number; maxCount?: number; incoming?: string } = {},
): string[] {
  const maxBytes = opts.maxBytes ?? MAX_BYTES
  const maxCount = opts.maxCount ?? MAX_COUNT
  const ordered = [...entries].sort((a, b) => a.lastUsedAt - b.lastUsedAt)
  let totalBytes = ordered.reduce((sum, e) => sum + e.bytes, 0)
  let count = ordered.length
  const drop: string[] = []
  for (const entry of ordered) {
    if (totalBytes <= maxBytes && count <= maxCount) break
    if (entry.fileId === opts.incoming) continue
    drop.push(entry.fileId)
    totalBytes -= entry.bytes
    count -= 1
  }
  return drop
}

function hasIndexedDB(): boolean {
  return typeof indexedDB !== 'undefined'
}

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION)
    req.onupgradeneeded = () => {
      if (!req.result.objectStoreNames.contains(STORE)) {
        req.result.createObjectStore(STORE, { keyPath: 'fileId' })
      }
    }
    req.onsuccess = () => resolve(req.result)
    req.onerror = () => reject(req.error ?? new Error('IndexedDB open failed'))
  })
}

function tx<T>(
  db: IDBDatabase,
  mode: IDBTransactionMode,
  run: (store: IDBObjectStore) => IDBRequest<T> | void,
): Promise<T | undefined> {
  return new Promise((resolve, reject) => {
    const t = db.transaction(STORE, mode)
    const store = t.objectStore(STORE)
    let result: T | undefined
    const req = run(store)
    if (req) req.onsuccess = () => (result = req.result)
    t.oncomplete = () => resolve(result)
    t.onerror = () => reject(t.error ?? new Error('IndexedDB transaction failed'))
  })
}

export async function getCachedBook(fileId: string): Promise<Blob | null> {
  if (!hasIndexedDB()) return null
  try {
    const db = await openDb()
    const record = (await tx<CacheRecord>(db, 'readonly', (s) => s.get(fileId))) as
      | CacheRecord
      | undefined
    if (!record) {
      db.close()
      return null
    }
    // touch lastUsedAt (best-effort — a failed touch just means it looks
    // older than it is next time we evict).
    await tx(db, 'readwrite', (s) => s.put({ ...record, lastUsedAt: Date.now() })).catch(
      () => undefined,
    )
    db.close()
    return record.blob
  } catch {
    return null
  }
}

export async function putCachedBook(fileId: string, blob: Blob): Promise<void> {
  if (!hasIndexedDB()) return
  try {
    const db = await openDb()
    const now = Date.now()
    await tx(db, 'readwrite', (s) =>
      s.put({ fileId, blob, bytes: blob.size, savedAt: now, lastUsedAt: now } satisfies CacheRecord),
    )
    const all = ((await tx<CacheRecord[]>(db, 'readonly', (s) => s.getAll())) ?? []) as CacheRecord[]
    const drop = evictionPlan(all, { incoming: fileId })
    for (const id of drop) {
      await tx(db, 'readwrite', (s) => s.delete(id)).catch(() => undefined)
    }
    db.close()
  } catch {
    // A full or unavailable cache must never break opening a book — the
    // reader falls back to re-downloading next time.
  }
}

export async function cacheStats(): Promise<{ count: number; bytes: number }> {
  if (!hasIndexedDB()) return { count: 0, bytes: 0 }
  try {
    const db = await openDb()
    const all = ((await tx<CacheRecord[]>(db, 'readonly', (s) => s.getAll())) ?? []) as CacheRecord[]
    db.close()
    return { count: all.length, bytes: all.reduce((sum, e) => sum + e.bytes, 0) }
  } catch {
    return { count: 0, bytes: 0 }
  }
}

export async function clearBookCache(): Promise<void> {
  if (!hasIndexedDB()) return
  try {
    const db = await openDb()
    await tx(db, 'readwrite', (s) => s.clear())
    db.close()
  } catch {
    // ignore
  }
}
