// bookbrain-new-releases.json — Hardcover's recent + near-future books for
// the authors James reads, minus what's already owned or wishlisted
// (prompts/27 Part 2, written by library_index_service.regenerate_new_releases).
//
// A separate sidecar from bookbrain-index.json, same lazy modifiedTime-gated
// localStorage-cache pattern as recommendations.ts — App fetches it once on
// first idle, never blocking first paint. The viewer merges these `author`
// entries with the `series` entries it derives itself (collectSeriesReleases)
// into one feed for the release strips + <NewReleasesScreen>.

import type { ReleaseItem } from './releases'

const FILENAME = 'bookbrain-new-releases.json'
const CACHE_KEY = 'bookbrain.newReleases'

export interface NewReleases {
  recent: ReleaseItem[]
  upcoming: ReleaseItem[]
}

export const EMPTY_NEW_RELEASES: NewReleases = { recent: [], upcoming: [] }

interface RawItem {
  title?: string
  author?: string | null
  isbn13?: string | null
  releaseDate?: string | null
  genres?: string[]
  hardcoverSlug?: string | null
}

interface RawFile {
  version?: number
  recent?: RawItem[]
  upcoming?: RawItem[]
}

interface Cached {
  libraryFolderId: string
  modifiedTime: string | null
  releases: NewReleases
}

function toItem(raw: RawItem): ReleaseItem | null {
  if (typeof raw.title !== 'string' || !raw.title) return null
  const author = raw.author ?? null
  return {
    key: raw.isbn13 || `author:${raw.title.toLowerCase()}|${(author ?? '').toLowerCase()}`,
    title: raw.title,
    author,
    series: null,
    seriesPosition: null,
    isbn13: typeof raw.isbn13 === 'string' ? raw.isbn13 : null,
    releaseDate: typeof raw.releaseDate === 'string' ? raw.releaseDate : null,
    description: null,
    hardcoverSlug: typeof raw.hardcoverSlug === 'string' ? raw.hardcoverSlug : null,
    genres: Array.isArray(raw.genres) ? raw.genres.filter((g): g is string => typeof g === 'string') : [],
    source: 'author',
  }
}

export function normaliseNewReleases(raw: RawFile): NewReleases {
  const list = (arr: RawItem[] | undefined) =>
    (arr ?? []).map(toItem).filter((i): i is ReleaseItem => i !== null)
  return { recent: list(raw.recent), upcoming: list(raw.upcoming) }
}

function readCache(): Cached | null {
  try {
    const raw = localStorage.getItem(CACHE_KEY)
    return raw ? (JSON.parse(raw) as Cached) : null
  } catch {
    return null
  }
}

export function clearNewReleasesCache(): void {
  try {
    localStorage.removeItem(CACHE_KEY)
  } catch {
    /* private mode */
  }
}

export async function fetchNewReleases(
  token: string,
  libraryFolderId: string,
): Promise<NewReleases> {
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
    if (files.length === 0) return cacheValid ? cached.releases : EMPTY_NEW_RELEASES

    const { id, modifiedTime } = files[0]
    if (cacheValid && cached.modifiedTime === modifiedTime) return cached.releases

    const fileResp = await fetch(`https://www.googleapis.com/drive/v3/files/${id}?alt=media`, {
      headers: { Authorization: `Bearer ${token}` },
    })
    if (!fileResp.ok) throw new Error(`download ${fileResp.status}`)
    const releases = normaliseNewReleases((await fileResp.json()) as RawFile)
    try {
      localStorage.setItem(CACHE_KEY, JSON.stringify({ libraryFolderId, modifiedTime, releases }))
    } catch {
      /* over quota / private mode */
    }
    return releases
  } catch {
    return cacheValid ? cached.releases : EMPTY_NEW_RELEASES
  }
}
