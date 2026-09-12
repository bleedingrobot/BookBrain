import {
  FOLDER_MIME_TYPE,
  getStartPageToken,
  isSupportedEbook,
  listAllChanges,
  listLibraryTree,
  StalePageTokenError,
  type DriveChange,
  type DriveFile,
} from './drive'

// The cached Drive file listing lives in IndexedDB, not localStorage —
// localStorage's few-MB-per-origin quota is easy for a large library's raw
// file listing to blow past (id + name for every file *and* folder in the
// tree), and a failed write there used to throw all the way up into the UI
// instead of just meaning "redo the full walk next time". IndexedDB's quota
// is tied to actual disk space, the same reasoning bookCache.ts already
// uses for the (much bigger) downloaded EPUB bytes.
const DB_NAME = 'bookbrain-library'
const STORE = 'cache'
const DB_VERSION = 1
const RECORD_KEY = 'library'
const LEGACY_LOCALSTORAGE_KEY = 'bookbrain.libraryCache'

// Incremental sync trusts Drive's changes feed and our own diffing logic to
// stay correct forever — but a bug in either (we've already had to fix one:
// see the "brand-new nested folder" fix) can silently drop something and
// then advance the sync token past it. Once that happens it's permanently
// invisible to Refresh, since nothing about the dropped item changes again
// afterward for Drive to report. A periodic full walk is the only thing
// that can catch and correct that kind of silent drift without the user
// ever noticing something's missing.
const AUTO_REBUILD_INTERVAL_MS = 24 * 60 * 60 * 1000

export interface LibraryCache {
  libraryFolderId: string
  pageToken: string
  files: DriveFile[]
  folderIds: string[]
  builtAt: number
}

function hasIndexedDB(): boolean {
  return typeof indexedDB !== 'undefined'
}

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION)
    req.onupgradeneeded = () => {
      if (!req.result.objectStoreNames.contains(STORE)) {
        req.result.createObjectStore(STORE)
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

// Carry-over for anyone with a pre-migration cache still sitting in
// localStorage, so shipping this doesn't force every existing browser into a
// full rebuild. A library too big to have fit there in the first place — the
// exact case this migration exists for — simply has nothing to carry over.
// No "already migrated" flag needed: the localStorage read below is cheap
// and synchronous, and it's a no-op the moment the legacy key is gone (which
// removing it below makes true from the second call on).
async function migrateFromLocalStorage(): Promise<void> {
  let raw: string | null = null
  try {
    raw = localStorage.getItem(LEGACY_LOCALSTORAGE_KEY)
  } catch {
    return
  }
  if (!raw) return
  try {
    await saveCache(JSON.parse(raw) as LibraryCache)
  } catch {
    // Corrupt — nothing worth carrying over; still clear it below.
  }
  try {
    localStorage.removeItem(LEGACY_LOCALSTORAGE_KEY)
  } catch {
    // ignore
  }
}

async function loadCache(): Promise<LibraryCache | null> {
  await migrateFromLocalStorage()
  if (!hasIndexedDB()) return null
  try {
    const db = await openDb()
    const record = await tx<LibraryCache>(db, 'readonly', (s) => s.get(RECORD_KEY))
    db.close()
    return record ?? null
  } catch {
    return null
  }
}

// Exported for tests to seed an "existing cache" scenario directly, rather
// than reaching for the legacy localStorage key this migrated away from.
export async function saveCache(cache: LibraryCache): Promise<void> {
  if (!hasIndexedDB()) return
  try {
    const db = await openDb()
    await tx(db, 'readwrite', (s) => s.put(cache, RECORD_KEY))
    db.close()
  } catch {
    // Best-effort, same as before — IndexedDB has far more headroom than
    // localStorage but still isn't guaranteed (private browsing, a full
    // disk). Losing this just means the next load redoes the full Drive walk.
  }
}

export async function clearLibraryCache(): Promise<void> {
  if (!hasIndexedDB()) return
  try {
    const db = await openDb()
    await tx(db, 'readwrite', (s) => s.clear())
    db.close()
  } catch {
    // ignore
  }
}

export async function loadCachedFiles(libraryFolderId: string): Promise<DriveFile[] | null> {
  const cache = await loadCache()
  if (!cache || cache.libraryFolderId !== libraryFolderId) return null
  return cache.files
}

async function fullRebuild(token: string, libraryFolderId: string): Promise<LibraryCache> {
  // Grab the sync token BEFORE walking the tree, so anything that changes
  // mid-walk isn't silently missed — it'll just be reported again
  // (harmlessly) on the very next incremental sync.
  const pageToken = await getStartPageToken(token)
  const { files, folderIds } = await listLibraryTree(token, libraryFolderId)
  const cache: LibraryCache = { libraryFolderId, pageToken, files, folderIds, builtAt: Date.now() }
  await saveCache(cache)
  return cache
}

export function applyChanges(cache: LibraryCache, changes: DriveChange[]): LibraryCache {
  const filesById = new Map(cache.files.map((f) => [f.id, f]))
  const folderIds = new Set(cache.folderIds)

  // Removals/trashing apply regardless of type or ordering.
  for (const change of changes) {
    if (change.removed || !change.file || change.file.trashed) {
      filesById.delete(change.fileId)
      folderIds.delete(change.fileId)
    }
  }

  const live = changes.filter((c) => !c.removed && c.file && !c.file.trashed)

  // Folders first, then files. A single pass over folder changes isn't
  // enough on its own: organizing into a brand-new author AND a brand-new
  // series creates both folders in the same batch (nested — the series
  // folder's parent is the author folder), and Drive's changes.list gives
  // no ordering guarantee between the two. If the series folder's change
  // happened to be listed before the author folder's, a single pass would
  // permanently reject it (parent not known *yet*) and every file inside
  // it would silently vanish from an incremental Refresh — recoverable
  // only via a full Rebuild, which walks the tree fresh instead of
  // depending on this ordering at all. So: keep re-resolving newly-known
  // parents until a full pass adds nothing further, then anything still
  // unresolved genuinely isn't in the tree (or was moved out of it).
  const folderChanges = live.filter((c) => c.file!.mimeType === FOLDER_MIME_TYPE)
  let resolvedMore = true
  while (resolvedMore) {
    resolvedMore = false
    for (const change of folderChanges) {
      const file = change.file!
      if (folderIds.has(file.id)) continue
      if ((file.parents ?? []).some((p) => folderIds.has(p))) {
        folderIds.add(file.id)
        resolvedMore = true
      }
    }
  }
  for (const change of folderChanges) {
    const file = change.file!
    // `parents` absent (not `[]`) means the change record carries no
    // placement info — Drive doesn't send `parents` on every delta. Don't
    // evict a folder we already know over that; only an explicit
    // removed/trashed (handled above) or a populated `parents` with no known
    // ancestor means it genuinely left the tree.
    if (file.parents === undefined) continue
    if (!file.parents.some((p) => folderIds.has(p))) {
      folderIds.delete(file.id) // not ours, or moved out of the tree
    }
  }

  for (const change of live) {
    const file = change.file!
    if (file.mimeType === FOLDER_MIME_TYPE) continue
    // Same missing-`parents` guard as the folder pass: a change with no
    // `parents` key tells us nothing about where the file is — leave any
    // existing cache entry alone rather than dropping it until the 24h
    // rebuild. `parents: []` (explicitly empty) still means "not in our
    // tree" and is handled by the `.some` below.
    if (file.parents === undefined) continue
    const parentKnown = file.parents.some((p) => folderIds.has(p))
    if (parentKnown && isSupportedEbook(file.name)) {
      filesById.set(file.id, { id: file.id, name: file.name })
    } else {
      filesById.delete(file.id) // moved out of the library, renamed away from .epub/.kpub, etc.
    }
  }

  return { ...cache, files: Array.from(filesById.values()), folderIds: Array.from(folderIds) }
}

export async function syncLibrary(
  token: string,
  libraryFolderId: string,
): Promise<{ cache: LibraryCache; rebuilt: boolean }> {
  const existing = await loadCache()

  if (!existing || existing.libraryFolderId !== libraryFolderId) {
    return { cache: await fullRebuild(token, libraryFolderId), rebuilt: true }
  }

  if (!existing.builtAt || Date.now() - existing.builtAt > AUTO_REBUILD_INTERVAL_MS) {
    return { cache: await fullRebuild(token, libraryFolderId), rebuilt: true }
  }

  try {
    const { changes, newStartPageToken } = await listAllChanges(token, existing.pageToken)
    const cache: LibraryCache = { ...applyChanges(existing, changes), pageToken: newStartPageToken }
    await saveCache(cache)
    return { cache, rebuilt: false }
  } catch (err) {
    // A *stale sync token* is the one thing a full rebuild fixes — Drive only
    // keeps change history for a limited window. Every other failure (a
    // transient 500, offline, a rate-limit) must surface: a blind rebuild
    // here would fire hundreds of Drive calls and hide the real problem. The
    // caller (useLibrary.runSync) keeps showing the last cached list + an
    // error, and the 24h auto-rebuild above is the backstop for silent drift.
    if (err instanceof StalePageTokenError) {
      return { cache: await fullRebuild(token, libraryFolderId), rebuilt: true }
    }
    throw err
  }
}
