// bookbrain-prompts.json — Hardcover "Prompts" (community questions with book
// answers) mapped to the books James owns that answer them (prompts/31 Part F,
// written by library_index_service.regenerate_prompts). Only questions with
// >= 2 owned answers are kept.
//
// Same lazy modifiedTime-gated localStorage cache as news.ts / newReleases.ts.

import { fetchDriveBytes, findSidecarMeta } from './drive'

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
    const meta = await findSidecarMeta(token, libraryFolderId, FILENAME)
    if (!meta) return cacheValid ? cached.prompts : EMPTY_PROMPTS
    if (cacheValid && cached.modifiedTime === meta.modifiedTime) return cached.prompts

    const buf = await fetchDriveBytes(token, meta.id)
    const prompts = normalisePrompts(JSON.parse(new TextDecoder().decode(buf)) as RawFile)
    try {
      localStorage.setItem(
        CACHE_KEY,
        JSON.stringify({ libraryFolderId, modifiedTime: meta.modifiedTime, prompts }),
      )
    } catch {
      /* over quota / private mode */
    }
    return prompts
  } catch {
    return cacheValid ? cached.prompts : EMPTY_PROMPTS
  }
}
