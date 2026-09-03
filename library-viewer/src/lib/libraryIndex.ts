// bookbrain-index.json is a sidecar the backend drops in the library root
// on every organize/rebuild: structured metadata (author, series,
// description, added-date) keyed by Drive file id. It's strictly optional —
// when it's missing or a book isn't in it, the viewer falls back to parsing
// the organized filename. See backend/app/services/library_index_service.py.

const INDEX_FILENAME = 'bookbrain-index.json'
const CACHE_KEY = 'bookbrain.metadataIndex'

export interface IndexEntry {
  title: string
  author: string | null
  series: string | null
  seriesNumber: number | null
  description: string | null
  addedAt: string | null
}

export type LibraryIndex = Record<string, IndexEntry>

interface CachedIndex {
  libraryFolderId: string
  modifiedTime: string | null
  index: LibraryIndex
}

interface RawIndexFile {
  version?: number
  books?: Record<string, Partial<IndexEntry>>
}

function normalise(raw: RawIndexFile): LibraryIndex {
  const out: LibraryIndex = {}
  for (const [id, entry] of Object.entries(raw.books ?? {})) {
    if (!entry || typeof entry.title !== 'string') continue
    out[id] = {
      title: entry.title,
      author: entry.author ?? null,
      series: entry.series ?? null,
      seriesNumber: typeof entry.seriesNumber === 'number' ? entry.seriesNumber : null,
      description: entry.description ?? null,
      addedAt: entry.addedAt ?? null,
    }
  }
  return out
}

function readCache(): CachedIndex | null {
  const raw = localStorage.getItem(CACHE_KEY)
  if (!raw) return null
  try {
    return JSON.parse(raw) as CachedIndex
  } catch {
    return null
  }
}

export function loadCachedIndex(libraryFolderId: string): LibraryIndex {
  const cached = readCache()
  return cached && cached.libraryFolderId === libraryFolderId ? cached.index : {}
}

export function clearCachedIndex(): void {
  localStorage.removeItem(CACHE_KEY)
}

// Best-effort: any failure (no sidecar yet, offline, malformed) resolves to
// the last cached copy so a transient Drive hiccup doesn't wipe metadata
// mid-session. The full body (~1MB+ for a big library) is only downloaded
// when the file's modifiedTime differs from what we last cached — every
// other sync is just one tiny metadata request.
export async function fetchLibraryIndex(
  token: string,
  libraryFolderId: string,
): Promise<LibraryIndex> {
  const cached = readCache()
  const cacheValid = cached?.libraryFolderId === libraryFolderId
  try {
    const query = encodeURIComponent(
      `'${libraryFolderId}' in parents and name = '${INDEX_FILENAME}' and trashed = false`,
    )
    const listResp = await fetch(
      `https://www.googleapis.com/drive/v3/files?q=${query}&fields=files(id,modifiedTime)&pageSize=1`,
      { headers: { Authorization: `Bearer ${token}` } },
    )
    if (!listResp.ok) throw new Error(`list ${listResp.status}`)
    const { files } = (await listResp.json()) as {
      files: { id: string; modifiedTime: string }[]
    }
    if (files.length === 0) return cacheValid ? cached.index : {}

    const { id, modifiedTime } = files[0]
    if (cacheValid && cached.modifiedTime === modifiedTime) return cached.index

    const fileResp = await fetch(`https://www.googleapis.com/drive/v3/files/${id}?alt=media`, {
      headers: { Authorization: `Bearer ${token}` },
    })
    if (!fileResp.ok) throw new Error(`download ${fileResp.status}`)
    const index = normalise((await fileResp.json()) as RawIndexFile)

    const next: CachedIndex = { libraryFolderId, modifiedTime, index }
    localStorage.setItem(CACHE_KEY, JSON.stringify(next))
    return index
  } catch {
    return cacheValid ? cached.index : {}
  }
}
