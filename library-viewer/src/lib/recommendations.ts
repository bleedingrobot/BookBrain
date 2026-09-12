// bookbrain-recommendations.json — Hardcover's "readers also liked" per
// organised file (prompts/25 Phase 3). A separate sidecar from
// bookbrain-index.json so it's only fetched when a book row is actually
// expanded, keeping the main index download lean. Best-effort + cached,
// same modifiedTime-gated pattern as libraryIndex.ts.

const FILENAME = 'bookbrain-recommendations.json'
const CACHE_KEY = 'bookbrain.recommendations'

export interface RecBook {
  title: string
  author: string | null
  isbn13: string | null
}

export type Recommendations = Record<string, RecBook[]> // keyed by drive file id

interface RawFile {
  version?: number
  books?: Record<string, Partial<RecBook>[]>
}

interface Cached {
  libraryFolderId: string
  modifiedTime: string | null
  recs: Recommendations
}

export function normaliseRecs(raw: RawFile): Recommendations {
  const out: Recommendations = {}
  for (const [id, list] of Object.entries(raw.books ?? {})) {
    if (!Array.isArray(list)) continue
    const clean = list
      .filter((r): r is RecBook => !!r && typeof r.title === 'string')
      .map((r) => ({
        title: r.title,
        author: r.author ?? null,
        isbn13: typeof r.isbn13 === 'string' ? r.isbn13 : null,
      }))
    if (clean.length > 0) out[id] = clean
  }
  return out
}

function readCache(): Cached | null {
  try {
    const raw = localStorage.getItem(CACHE_KEY)
    return raw ? (JSON.parse(raw) as Cached) : null
  } catch {
    return null
  }
}

export function clearRecommendationsCache(): void {
  try {
    localStorage.removeItem(CACHE_KEY)
  } catch {
    /* private mode */
  }
}

export async function fetchRecommendations(
  token: string,
  libraryFolderId: string,
): Promise<Recommendations> {
  const cached = readCache()
  const cacheValid = cached?.libraryFolderId === libraryFolderId
  try {
    const query = encodeURIComponent(
      `'${libraryFolderId}' in parents and name = '${FILENAME}' and trashed = false`,
    )
    const listResp = await fetch(
      `https://www.googleapis.com/drive/v3/files?q=${query}&fields=files(id,modifiedTime)&pageSize=1`,
      { headers: { Authorization: `Bearer ${token}` } },
    )
    if (!listResp.ok) throw new Error(`list ${listResp.status}`)
    const { files } = (await listResp.json()) as { files: { id: string; modifiedTime: string }[] }
    if (files.length === 0) return cacheValid ? cached.recs : {}

    const { id, modifiedTime } = files[0]
    if (cacheValid && cached.modifiedTime === modifiedTime) return cached.recs

    const fileResp = await fetch(`https://www.googleapis.com/drive/v3/files/${id}?alt=media`, {
      headers: { Authorization: `Bearer ${token}` },
    })
    if (!fileResp.ok) throw new Error(`download ${fileResp.status}`)
    const recs = normaliseRecs((await fileResp.json()) as RawFile)
    try {
      localStorage.setItem(CACHE_KEY, JSON.stringify({ libraryFolderId, modifiedTime, recs }))
    } catch {
      /* over quota / private mode — fine, we just re-fetch next time */
    }
    return recs
  } catch {
    return cacheValid ? cached.recs : {}
  }
}
