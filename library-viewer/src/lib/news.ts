// bookbrain-news.json — the latest articles from a curated set of SFF news /
// review feeds, fetched + parsed by the backend (the static viewer can't:
// none of the feeds send CORS headers). prompts/32, written by
// library_index_service.regenerate_news.
//
// Same lazy modifiedTime-gated localStorage cache as newReleases.ts. Every
// item links out to the original — we only ever store headline + a short
// excerpt + attribution.

import { readJsonFile, writeJsonFile } from './drive'

const FILENAME = 'bookbrain-news.json'
const CACHE_KEY = 'bookbrain.news'
// Dismissed article links live in a Drive sidecar so they sync across devices;
// localStorage is just a cache for instant first render / offline.
const DISMISSED_FILENAME = 'bookbrain-news-dismissed.json'
const DISMISS_CACHE_KEY = 'bookbrain.newsDismissed'

export interface NewsItem {
  title: string
  link: string
  summary: string
  published: string | null // ISO 8601, or null
  source: string
}

export interface News {
  generatedAt: string | null
  items: NewsItem[]
}

export const EMPTY_NEWS: News = { generatedAt: null, items: [] }

interface RawItem {
  title?: unknown
  link?: unknown
  summary?: unknown
  published?: unknown
  source?: unknown
}

interface RawFile {
  version?: number
  generatedAt?: string
  items?: RawItem[]
}

interface Cached {
  libraryFolderId: string
  modifiedTime: string | null
  news: News
}

export function normaliseNews(raw: RawFile): News {
  const items: NewsItem[] = []
  for (const r of raw.items ?? []) {
    if (!r || typeof r.title !== 'string' || typeof r.link !== 'string') continue
    if (!r.title.trim() || !/^https?:\/\//.test(r.link)) continue
    items.push({
      title: r.title,
      link: r.link,
      summary: typeof r.summary === 'string' ? r.summary : '',
      published: typeof r.published === 'string' ? r.published : null,
      source: typeof r.source === 'string' ? r.source : 'SFF news',
    })
  }
  return {
    generatedAt: typeof raw.generatedAt === 'string' ? raw.generatedAt : null,
    items,
  }
}

function readCache(): Cached | null {
  try {
    const raw = localStorage.getItem(CACHE_KEY)
    return raw ? (JSON.parse(raw) as Cached) : null
  } catch {
    return null
  }
}

export function clearNewsCache(): void {
  try {
    localStorage.removeItem(CACHE_KEY)
  } catch {
    /* private mode */
  }
}

// --- dismissed articles (synced via a Drive sidecar) ----------------------

interface DismissedFile {
  version?: number
  links?: unknown
}

function readDismissCache(): Set<string> {
  try {
    const raw = localStorage.getItem(DISMISS_CACHE_KEY)
    const arr = raw ? (JSON.parse(raw) as unknown) : []
    return new Set(Array.isArray(arr) ? arr.filter((x): x is string => typeof x === 'string') : [])
  } catch {
    return new Set()
  }
}

function writeDismissCache(links: Set<string>): void {
  try {
    localStorage.setItem(DISMISS_CACHE_KEY, JSON.stringify([...links]))
  } catch {
    /* private mode / over quota */
  }
}

function parseDismissed(content: DismissedFile | null | undefined): Set<string> {
  const arr = content?.links
  return new Set(Array.isArray(arr) ? arr.filter((x): x is string => typeof x === 'string') : [])
}

// Instant, synchronous — the cached set, for the first render before Drive
// answers. `dismissedNewsSynced` then refreshes it from the sidecar.
export function cachedDismissedNews(): Set<string> {
  return readDismissCache()
}

// The current dismissed set from the Drive sidecar. Merges in whatever the
// cache holds (so an older localStorage-only install seeds the sidecar rather
// than losing its dismissals), refreshes the cache, and returns the union.
// Any failure falls back to the cache untouched.
export async function dismissedNewsSynced(
  token: string,
  libraryFolderId: string,
): Promise<Set<string>> {
  const cached = readDismissCache()
  try {
    const found = await readJsonFile<DismissedFile>(token, libraryFolderId, DISMISSED_FILENAME)
    const links = parseDismissed(found?.content)
    for (const l of cached) links.add(l)
    writeDismissCache(links)
    // Seed / top up the sidecar when the cache had links it didn't.
    if (links.size !== parseDismissed(found?.content).size) {
      try {
        await writeJsonFile(
          token,
          libraryFolderId,
          DISMISSED_FILENAME,
          { version: 1, links: [...links] },
          found?.id ?? null,
        )
      } catch {
        /* best-effort seed */
      }
    }
    return links
  } catch {
    return cached
  }
}

// Serialise sidecar writes so rapid X-ing doesn't clobber (no Drive locking).
let dismissWriteChain: Promise<unknown> = Promise.resolve()

// Add `link` to the sidecar (read → merge with the server's copy + `current` →
// write), so a dismissal from another device isn't clobbered. Best-effort:
// updates the cache + returns the new set even if the Drive write fails.
export function dismissNewsItem(
  token: string,
  libraryFolderId: string,
  link: string,
  current: Set<string>,
): Promise<Set<string>> {
  const optimistic = new Set(current).add(link)
  writeDismissCache(optimistic)
  const run = dismissWriteChain.then(async () => {
    try {
      const found = await readJsonFile<DismissedFile>(token, libraryFolderId, DISMISSED_FILENAME)
      const merged = parseDismissed(found?.content)
      for (const l of optimistic) merged.add(l)
      for (const l of readDismissCache()) merged.add(l) // any siblings queued meanwhile
      await writeJsonFile(
        token,
        libraryFolderId,
        DISMISSED_FILENAME,
        { version: 1, links: [...merged] },
        found?.id ?? null,
      )
      writeDismissCache(merged)
      return merged
    } catch {
      return optimistic
    }
  })
  dismissWriteChain = run.catch(() => undefined)
  return run
}

// Drop dismissed links no longer in any feed — once an article has aged out
// everywhere it can't come back, so the list needn't grow forever. Writes the
// sidecar only when something actually changed.
export async function pruneDismissedNews(
  token: string,
  libraryFolderId: string,
  current: Set<string>,
  liveLinks: string[],
): Promise<Set<string>> {
  if (current.size === 0) return current
  const live = new Set(liveLinks)
  const next = new Set([...current].filter((l) => live.has(l)))
  if (next.size === current.size) return current
  writeDismissCache(next)
  try {
    const found = await readJsonFile<DismissedFile>(token, libraryFolderId, DISMISSED_FILENAME)
    await writeJsonFile(
      token,
      libraryFolderId,
      DISMISSED_FILENAME,
      { version: 1, links: [...next] },
      found?.id ?? null,
    )
  } catch {
    /* the cache still narrowed; retried next prune */
  }
  return next
}

export async function fetchNews(token: string, libraryFolderId: string): Promise<News> {
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
    if (files.length === 0) return cacheValid ? cached.news : EMPTY_NEWS

    const { id, modifiedTime } = files[0]
    if (cacheValid && cached.modifiedTime === modifiedTime) return cached.news

    const fileResp = await fetch(`https://www.googleapis.com/drive/v3/files/${id}?alt=media`, {
      headers: { Authorization: `Bearer ${token}` },
    })
    if (!fileResp.ok) throw new Error(`download ${fileResp.status}`)
    const news = normaliseNews((await fileResp.json()) as RawFile)
    try {
      localStorage.setItem(CACHE_KEY, JSON.stringify({ libraryFolderId, modifiedTime, news }))
    } catch {
      /* over quota / private mode */
    }
    return news
  } catch {
    return cacheValid ? cached.news : EMPTY_NEWS
  }
}

// "3h ago" / "2d ago" / a date for anything older than ~2 weeks. Shared shape
// with WishlistScreen's whenText but tuned for article recency.
export function timeAgo(iso: string | null): string {
  if (!iso) return ''
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return ''
  const mins = Math.floor((Date.now() - then) / 60_000)
  if (mins < 60) return mins <= 1 ? 'just now' : `${mins}m ago`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  if (days < 14) return `${days}d ago`
  return new Date(iso).toLocaleDateString()
}
