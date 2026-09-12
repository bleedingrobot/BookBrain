// bookbrain-lists.json — curated Hardcover lists this library part-owns, plus
// the not-yet-owned books on them as wishlist candidates (prompts/31 Part E2,
// written by library_index_service.regenerate_lists).
//
// Same lazy modifiedTime-gated localStorage cache as news.ts / prompts.ts.

const FILENAME = 'bookbrain-lists.json'
const CACHE_KEY = 'bookbrain.lists'

export interface ListCandidate {
  title: string
  author: string | null
  isbn13: string | null
  fromList: string
}

export interface ListSummary {
  name: string
  slug: string | null
  owned: number
  total: number
}

export interface Lists {
  generatedAt: string | null
  lists: ListSummary[]
  candidates: ListCandidate[]
}

export const EMPTY_LISTS: Lists = { generatedAt: null, lists: [], candidates: [] }

interface RawFile {
  version?: number
  generatedAt?: string
  lists?: { name?: unknown; slug?: unknown; owned?: unknown; total?: unknown }[]
  candidates?: { title?: unknown; author?: unknown; isbn13?: unknown; fromList?: unknown }[]
}

export function normaliseLists(raw: RawFile): Lists {
  const lists: ListSummary[] = []
  for (const l of raw.lists ?? []) {
    if (!l || typeof l.name !== 'string' || !l.name.trim()) continue
    lists.push({
      name: l.name,
      slug: typeof l.slug === 'string' ? l.slug : null,
      owned: typeof l.owned === 'number' ? l.owned : 0,
      total: typeof l.total === 'number' ? l.total : 0,
    })
  }
  const candidates: ListCandidate[] = []
  for (const c of raw.candidates ?? []) {
    if (!c || typeof c.title !== 'string' || !c.title.trim()) continue
    candidates.push({
      title: c.title,
      author: typeof c.author === 'string' ? c.author : null,
      isbn13: typeof c.isbn13 === 'string' ? c.isbn13 : null,
      fromList: typeof c.fromList === 'string' ? c.fromList : '',
    })
  }
  return {
    generatedAt: typeof raw.generatedAt === 'string' ? raw.generatedAt : null,
    lists,
    candidates,
  }
}

interface Cached {
  libraryFolderId: string
  modifiedTime: string | null
  lists: Lists
}

function readCache(): Cached | null {
  try {
    const raw = localStorage.getItem(CACHE_KEY)
    return raw ? (JSON.parse(raw) as Cached) : null
  } catch {
    return null
  }
}

export function clearListsCache(): void {
  try {
    localStorage.removeItem(CACHE_KEY)
  } catch {
    /* private mode */
  }
}

export async function fetchLists(token: string, libraryFolderId: string): Promise<Lists> {
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
    if (files.length === 0) return cacheValid ? cached.lists : EMPTY_LISTS

    const { id, modifiedTime } = files[0]
    if (cacheValid && cached.modifiedTime === modifiedTime) return cached.lists

    const fileResp = await fetch(`https://www.googleapis.com/drive/v3/files/${id}?alt=media`, {
      headers: { Authorization: `Bearer ${token}` },
    })
    if (!fileResp.ok) throw new Error(`download ${fileResp.status}`)
    const lists = normaliseLists((await fileResp.json()) as RawFile)
    try {
      localStorage.setItem(CACHE_KEY, JSON.stringify({ libraryFolderId, modifiedTime, lists }))
    } catch {
      /* over quota / private mode */
    }
    return lists
  } catch {
    return cacheValid ? cached.lists : EMPTY_LISTS
  }
}
