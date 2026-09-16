// bookbrain-prompts.json — Hardcover "Prompts" (community questions with book
// answers) mapped to the books James owns that answer them (prompts/31 Part F,
// written by library_index_service.regenerate_prompts). Only questions with
// >= 2 owned answers are kept.
//
// Same lazy modifiedTime-gated localStorage cache as news.ts / newReleases.ts.

const FILENAME = 'bookbrain-prompts.json'
const CACHE_KEY = 'bookbrain.prompts'

export interface LibraryPrompt {
  question: string
  slug: string | null
  driveIds: string[]
}

export interface Prompts {
  generatedAt: string | null
  prompts: LibraryPrompt[]
}

export const EMPTY_PROMPTS: Prompts = { generatedAt: null, prompts: [] }

interface RawFile {
  version?: number
  generatedAt?: string
  prompts?: { question?: unknown; slug?: unknown; driveIds?: unknown }[]
}

export function normalisePrompts(raw: RawFile): Prompts {
  const prompts: LibraryPrompt[] = []
  for (const p of raw.prompts ?? []) {
    if (!p || typeof p.question !== 'string' || !p.question.trim()) continue
    const driveIds = Array.isArray(p.driveIds)
      ? p.driveIds.filter((d): d is string => typeof d === 'string')
      : []
    if (driveIds.length < 2) continue
    prompts.push({
      question: p.question,
      slug: typeof p.slug === 'string' ? p.slug : null,
      driveIds,
    })
  }
  return {
    generatedAt: typeof raw.generatedAt === 'string' ? raw.generatedAt : null,
    prompts,
  }
}

interface Cached {
  libraryFolderId: string
  modifiedTime: string | null
  prompts: Prompts
}

function readCache(): Cached | null {
  try {
    const raw = localStorage.getItem(CACHE_KEY)
    return raw ? (JSON.parse(raw) as Cached) : null
  } catch {
    return null
  }
}

export function clearPromptsCache(): void {
  try {
    localStorage.removeItem(CACHE_KEY)
  } catch {
    /* private mode */
  }
}

export async function fetchPrompts(token: string, libraryFolderId: string): Promise<Prompts> {
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
    if (files.length === 0) return cacheValid ? cached.prompts : EMPTY_PROMPTS

    const { id, modifiedTime } = files[0]
    if (cacheValid && cached.modifiedTime === modifiedTime) return cached.prompts

    const fileResp = await fetch(`https://www.googleapis.com/drive/v3/files/${id}?alt=media`, {
      headers: { Authorization: `Bearer ${token}` },
    })
    if (!fileResp.ok) throw new Error(`download ${fileResp.status}`)
    const prompts = normalisePrompts((await fileResp.json()) as RawFile)
    try {
      localStorage.setItem(CACHE_KEY, JSON.stringify({ libraryFolderId, modifiedTime, prompts }))
    } catch {
      /* over quota / private mode */
    }
    return prompts
  } catch {
    return cacheValid ? cached.prompts : EMPTY_PROMPTS
  }
}
