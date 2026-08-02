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

export interface LibraryCache {
  libraryFolderId: string
  pageToken: string
  files: DriveFile[]
  folderIds: string[]
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
  const cache: LibraryCache = { libraryFolderId, pageToken, files, folderIds }
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

  // Folders first, then files — so a folder created and populated in the
  // same sync batch resolves correctly no matter which order Drive
  // reports the two changes in.
  for (const change of live) {
    const file = change.file!
    if (file.mimeType !== FOLDER_MIME_TYPE) continue
    const parentKnown = (file.parents ?? []).some((p) => folderIds.has(p))
    if (parentKnown) folderIds.add(file.id)
    else folderIds.delete(file.id) // not ours, or moved out of the tree
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
