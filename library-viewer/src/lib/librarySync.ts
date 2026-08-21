import {
  FOLDER_MIME_TYPE,
  getStartPageToken,
  isSupportedEbook,
  listAllChanges,
  listLibraryTree,
  type DriveChange,
  type DriveFile,
} from './drive'

const CACHE_KEY = 'bookbrain.libraryCache'

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

function loadCache(): LibraryCache | null {
  const raw = localStorage.getItem(CACHE_KEY)
  if (!raw) return null
  try {
    return JSON.parse(raw) as LibraryCache
  } catch {
    return null
  }
}

function saveCache(cache: LibraryCache): void {
  localStorage.setItem(CACHE_KEY, JSON.stringify(cache))
}

export function clearLibraryCache(): void {
  localStorage.removeItem(CACHE_KEY)
}

export function loadCachedFiles(libraryFolderId: string): DriveFile[] | null {
  const cache = loadCache()
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
  saveCache(cache)
  return cache
}

function applyChanges(cache: LibraryCache, changes: DriveChange[]): LibraryCache {
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
    if (!(file.parents ?? []).some((p) => folderIds.has(p))) {
      folderIds.delete(file.id) // not ours, or moved out of the tree
    }
  }

  for (const change of live) {
    const file = change.file!
    if (file.mimeType === FOLDER_MIME_TYPE) continue
    const parentKnown = (file.parents ?? []).some((p) => folderIds.has(p))
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
  const existing = loadCache()

  if (!existing || existing.libraryFolderId !== libraryFolderId) {
    return { cache: await fullRebuild(token, libraryFolderId), rebuilt: true }
  }

  if (!existing.builtAt || Date.now() - existing.builtAt > AUTO_REBUILD_INTERVAL_MS) {
    return { cache: await fullRebuild(token, libraryFolderId), rebuilt: true }
  }

  try {
    const { changes, newStartPageToken } = await listAllChanges(token, existing.pageToken)
    const cache: LibraryCache = { ...applyChanges(existing, changes), pageToken: newStartPageToken }
    saveCache(cache)
    return { cache, rebuilt: false }
  } catch {
    // The sync token can go stale — Drive only retains change history for
    // a limited window. Fall back to a full rebuild instead of surfacing
    // an error the user can't do anything about.
    return { cache: await fullRebuild(token, libraryFolderId), rebuilt: true }
  }
}
