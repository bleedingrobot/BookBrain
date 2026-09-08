// bookbrain-index.json is a sidecar the backend drops in the library root
// on every organize/rebuild: structured metadata (author, series,
// description, added-date, isbn) keyed by Drive file id, plus the id of the
// covers/ folder. It's strictly optional — when it's missing or a book
// isn't in it, the viewer falls back to parsing the organized filename.
// See backend/app/services/library_index_service.py.

const INDEX_FILENAME = 'bookbrain-index.json'
const CACHE_KEY = 'bookbrain.metadataIndex'

// bookbrain-index.json v4: Hardcover's curated per-book metadata (prompts/26
// Part B), shown as badges and offered as a genre facet. Every field is
// optional — only what Hardcover had is present.
export interface IndexMeta {
  rating: number | null
  ratingsCount: number | null
  pages: number | null
  category: string | null // "Novella" / "Graphic Novel" / "Light Novel" / …
  literaryType: string | null // "Fiction" | "Nonfiction"
  genres: string[]
  moods: string[]
}

export interface IndexEntry {
  title: string
  author: string | null
  series: string | null
  seriesNumber: number | null
  description: string | null
  addedAt: string | null
  isbn: string | null
  meta: IndexMeta | null
}

// bookbrain-index.json v3: Hardcover's canonical view of a series (prompts/25
// Phase 2), keyed by the same series name IndexEntry.series uses. Only series
// the backend could match to a Hardcover series appear here.
export interface SeriesCatalogBook {
  position: number
  title: string
}

export interface SeriesCatalog {
  hardcoverId: number | null
  hardcoverName: string | null
  hardcoverSlug: string | null
  primaryCount: number | null
  books: SeriesCatalogBook[]
}

export interface LibraryIndex {
  entries: Record<string, IndexEntry>
  series: Record<string, SeriesCatalog>
  coversFolder: string | null
}

export const EMPTY_INDEX: LibraryIndex = { entries: {}, series: {}, coversFolder: null }

interface CachedIndex {
  libraryFolderId: string
  modifiedTime: string | null
  index: LibraryIndex
}

export interface RawIndexFile {
  version?: number
  coversFolder?: string | null
  books?: Record<string, Omit<Partial<IndexEntry>, 'meta'> & { meta?: Partial<IndexMeta> | null }>
  series?: Record<string, Partial<SeriesCatalog>>
}

function normaliseMeta(raw: Partial<IndexMeta> | null | undefined): IndexMeta | null {
  if (!raw || typeof raw !== 'object') return null
  const strings = (v: unknown): string[] =>
    Array.isArray(v) ? v.filter((s): s is string => typeof s === 'string') : []
  const meta: IndexMeta = {
    rating: typeof raw.rating === 'number' ? raw.rating : null,
    ratingsCount: typeof raw.ratingsCount === 'number' ? raw.ratingsCount : null,
    pages: typeof raw.pages === 'number' ? raw.pages : null,
    category: typeof raw.category === 'string' ? raw.category : null,
    literaryType: typeof raw.literaryType === 'string' ? raw.literaryType : null,
    genres: strings(raw.genres),
    moods: strings(raw.moods),
  }
  const empty =
    meta.rating == null &&
    meta.ratingsCount == null &&
    meta.pages == null &&
    meta.category == null &&
    meta.literaryType == null &&
    meta.genres.length === 0 &&
    meta.moods.length === 0
  return empty ? null : meta
}

function normaliseSeries(raw: RawIndexFile['series']): Record<string, SeriesCatalog> {
  const out: Record<string, SeriesCatalog> = {}
  for (const [name, entry] of Object.entries(raw ?? {})) {
    if (!entry || !Array.isArray(entry.books)) continue
    const books = entry.books
      .filter(
        (b): b is SeriesCatalogBook =>
          !!b && typeof b.position === 'number' && typeof b.title === 'string',
      )
      .map((b) => ({ position: b.position, title: b.title }))
    if (books.length === 0) continue
    out[name] = {
      hardcoverId: typeof entry.hardcoverId === 'number' ? entry.hardcoverId : null,
      hardcoverName: entry.hardcoverName ?? null,
      hardcoverSlug: entry.hardcoverSlug ?? null,
      primaryCount: typeof entry.primaryCount === 'number' ? entry.primaryCount : null,
      books,
    }
  }
  return out
}

export function normalise(raw: RawIndexFile): LibraryIndex {
  const entries: Record<string, IndexEntry> = {}
  for (const [id, entry] of Object.entries(raw.books ?? {})) {
    if (!entry || typeof entry.title !== 'string') continue
    entries[id] = {
      title: entry.title,
      author: entry.author ?? null,
      series: entry.series ?? null,
      seriesNumber: typeof entry.seriesNumber === 'number' ? entry.seriesNumber : null,
      description: entry.description ?? null,
      addedAt: entry.addedAt ?? null,
      isbn: typeof entry.isbn === 'string' ? entry.isbn : null,
      meta: normaliseMeta(entry.meta),
    }
  }
  return { entries, series: normaliseSeries(raw.series), coversFolder: raw.coversFolder ?? null }
}

function readCache(): CachedIndex | null {
  const raw = localStorage.getItem(CACHE_KEY)
  if (!raw) return null
  try {
    const parsed = JSON.parse(raw) as CachedIndex
    // tolerate the pre-restructure cache shape
    if (parsed.index && !('entries' in parsed.index)) return null
    // a cache written before v3 has no `series` map
    if (parsed.index && !parsed.index.series) parsed.index.series = {}
    return parsed
  } catch {
    return null
  }
}

export function loadCachedIndex(libraryFolderId: string): LibraryIndex {
  const cached = readCache()
  return cached && cached.libraryFolderId === libraryFolderId ? cached.index : EMPTY_INDEX
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
    if (files.length === 0) return cacheValid ? cached.index : EMPTY_INDEX

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
    return cacheValid ? cached.index : EMPTY_INDEX
  }
}
