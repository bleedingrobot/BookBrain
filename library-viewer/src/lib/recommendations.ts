// bookbrain-recommendations.json — Hardcover's "readers also liked" per
// organised file (prompts/25 Phase 3). A separate sidecar from
// bookbrain-index.json so it's only fetched when a book row is actually
// expanded, keeping the main index download lean. Best-effort + cached,
// same modifiedTime-gated pattern as libraryIndex.ts.

import { fetchDriveBytes, findSidecarMeta } from './drive'

const FILENAME = 'bookbrain-recommendations.json'
const CACHE_KEY = 'bookbrain.recommendations'

export interface RecBook {
  title: string
  author: string | null
  isbn13: string | null
  // prompts/39 — present only for content_recs_service-derived entries
  // (tag-similar books already in this library, not Hardcover's external
  // "readers also liked"). `driveFileId` is an exact link — prefer it over
  // isbn/title matching when present. `sharedTags` powers the "because you
  // both have: …" explainer.
  driveFileId?: string
  sharedTags?: string[]
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
        driveFileId: typeof r.driveFileId === 'string' ? r.driveFileId : undefined,
        sharedTags: Array.isArray(r.sharedTags)
          ? r.sharedTags.filter((t): t is string => typeof t === 'string')
          : undefined,
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
    const meta = await findSidecarMeta(token, libraryFolderId, FILENAME)
    if (!meta) return cacheValid ? cached.recs : {}
    if (cacheValid && cached.modifiedTime === meta.modifiedTime) return cached.recs

    const buf = await fetchDriveBytes(token, meta.id)
    const recs = normaliseRecs(JSON.parse(new TextDecoder().decode(buf)) as RawFile)
    try {
      localStorage.setItem(
        CACHE_KEY,
        JSON.stringify({ libraryFolderId, modifiedTime: meta.modifiedTime, recs }),
      )
    } catch {
      /* over quota / private mode — fine, we just re-fetch next time */
    }
    return recs
  } catch {
    return cacheValid ? cached.recs : {}
  }
}
