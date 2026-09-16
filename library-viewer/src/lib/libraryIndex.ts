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
  // Crowd-sourced content warnings (prompts/31 Part A). Raw Hardcover tag
  // names, shown in a collapsed disclosure — informational, not a gate.
  contentWarnings: string[]
  // How many Hardcover lists this book appears on (prompts/31 Part E1) — a
  // rough "how canonical / talked-about is this" signal.
  listsCount: number | null
  // First-publication year and audiobook length (prompts/31 Part H).
  published: number | null
  audioHours: number | null
}

// bookbrain-index.json v9 — the local-LLM full-text catalogue entry
// (prompts/38), only ever present once a book's map-reduce pass has
// actually finished. `shortDescription` is spoiler-free by design;
// `longSummary` is not — the viewer should gate it behind an explicit
// reveal rather than showing it up front.
export interface LlmTags {
  ageRating: string | null
  genres: string[]
  moods: string[]
  themes: string[]
  representation: string[]
  contentWarnings: string[]
  confidenceNotes: string | null
  shortDescription: string | null
  longSummary: string | null
  generatedAt: string | null
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
  llmTags: LlmTags | null
}

// bookbrain-index.json v3: Hardcover's canonical view of a series (prompts/25
// Phase 2), keyed by the same series name IndexEntry.series uses. Only series
// the backend could match to a Hardcover series appear here.
export interface SeriesCatalogBook {
  position: number
  title: string
  // ISO date string, or null. Placeholder rows ("Untitled … #10", dated
  // decades out) are filtered viewer-side, not here. prompts/26 Part A.
  releaseDate?: string | null
  // The most-popular edition's ISBN-13, or null — lets the viewer pull an
  // Open Library cover for an entry that isn't in the library yet.
  // prompts/27 Part 1.
  isbn13?: string | null
}

export interface SeriesCatalog {
  hardcoverId: number | null
  hardcoverName: string | null
  hardcoverSlug: string | null
  primaryCount: number | null
  books: SeriesCatalogBook[]
}

// bookbrain-index.json v8: admin-defined smart shelves (a query rule
// resolved server-side — see backend/app/services/collection_rules.py),
// pre-resolved into a member list since the viewer has no backend of its own
// to evaluate a rule against.
export interface CollectionEntry {
  name: string
  description: string | null
  driveFileIds: string[]
}

export interface LibraryIndex {
  entries: Record<string, IndexEntry>
  series: Record<string, SeriesCatalog>
  collections: Record<string, CollectionEntry>
  coversFolder: string | null
}

export const EMPTY_INDEX: LibraryIndex = {
  entries: {},
  series: {},
  collections: {},
  coversFolder: null,
}

interface CachedIndex {
  libraryFolderId: string
  modifiedTime: string | null
  index: LibraryIndex
}

export interface RawIndexFile {
  version?: number
  coversFolder?: string | null
  books?: Record<
    string,
    Omit<Partial<IndexEntry>, 'meta' | 'llmTags'> & {
      meta?: Partial<IndexMeta> | null
      llmTags?: Partial<LlmTags> | null
    }
  >
  series?: Record<string, Partial<SeriesCatalog>>
  collections?: Record<string, Partial<CollectionEntry>>
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
    contentWarnings: strings(raw.contentWarnings),
    listsCount: typeof raw.listsCount === 'number' ? raw.listsCount : null,
    published: typeof raw.published === 'number' ? raw.published : null,
    audioHours: typeof raw.audioHours === 'number' ? raw.audioHours : null,
  }
  const empty =
    meta.rating == null &&
    meta.ratingsCount == null &&
    meta.pages == null &&
    meta.category == null &&
    meta.literaryType == null &&
    meta.genres.length === 0 &&
    meta.moods.length === 0 &&
    meta.contentWarnings.length === 0 &&
    meta.listsCount == null &&
    meta.published == null &&
    meta.audioHours == null
  return empty ? null : meta
}

function normaliseLlmTags(raw: Partial<LlmTags> | null | undefined): LlmTags | null {
  if (!raw || typeof raw !== 'object') return null
  const strings = (v: unknown): string[] =>
    Array.isArray(v) ? v.filter((s): s is string => typeof s === 'string') : []
  const str = (v: unknown): string | null => (typeof v === 'string' && v ? v : null)
  const tags: LlmTags = {
    ageRating: str(raw.ageRating),
    genres: strings(raw.genres),
    moods: strings(raw.moods),
    themes: strings(raw.themes),
    representation: strings(raw.representation),
    contentWarnings: strings(raw.contentWarnings),
    confidenceNotes: str(raw.confidenceNotes),
    shortDescription: str(raw.shortDescription),
    longSummary: str(raw.longSummary),
    generatedAt: str(raw.generatedAt),
  }
  // The one field the showcase actually requires — nothing sensible to show
  // without at least a blurb.
  return tags.shortDescription ? tags : null
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
      .map((b) => ({
        position: b.position,
        title: b.title,
        releaseDate: typeof b.releaseDate === 'string' ? b.releaseDate : null,
        isbn13: typeof b.isbn13 === 'string' ? b.isbn13 : null,
      }))
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

function normaliseCollections(raw: RawIndexFile['collections']): Record<string, CollectionEntry> {
  const out: Record<string, CollectionEntry> = {}
  for (const [id, entry] of Object.entries(raw ?? {})) {
    if (!entry || typeof entry.name !== 'string' || !Array.isArray(entry.driveFileIds)) continue
    const driveFileIds = entry.driveFileIds.filter((d): d is string => typeof d === 'string')
    if (driveFileIds.length === 0) continue
    out[id] = {
      name: entry.name,
      description: typeof entry.description === 'string' ? entry.description : null,
      driveFileIds,
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
      llmTags: normaliseLlmTags(entry.llmTags),
    }
  }
  return {
    entries,
    series: normaliseSeries(raw.series),
    collections: normaliseCollections(raw.collections),
    coversFolder: raw.coversFolder ?? null,
  }
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
    // a cache written before v8 has no `collections` map
    if (parsed.index && !parsed.index.collections) parsed.index.collections = {}
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
