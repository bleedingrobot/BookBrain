// bookbrain-news.json — the latest articles from a curated set of SFF news /
// review feeds, fetched + parsed by the backend (the static viewer can't:
// none of the feeds send CORS headers). prompts/32, written by
// library_index_service.regenerate_news.
//
// Same lazy modifiedTime-gated localStorage cache as newReleases.ts. Every
// item links out to the original — we only ever store headline + a short
// excerpt + attribution.

import { fetchDriveBytes, findSidecarMeta } from './drive'
import { makeSidecar } from './syncedSidecar'

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
//
// A set of article links the reader has X'd away � once cleared they stay
// cleared (unlike a "Read next" snooze). Goes through makeSidecar for the
// serialised write + concurrency guard (prompts/34).

interface DismissedFile {
  version?: number
  links?: unknown
}

function parseLinks(raw: unknown): string[] {
  const links = (raw as DismissedFile | null | undefined)?.links
  return Array.isArray(links) ? links.filter((x): x is string => typeof x === "string") : []
}

const dismissed = makeSidecar<string[]>({
  filename: DISMISSED_FILENAME,
  cacheKey: DISMISS_CACHE_KEY,
  empty: [],
  parse: parseLinks,
  serialise: (v) => ({ version: 1, links: [...new Set(v)].sort() }),
  merge: (a, b) => [...new Set([...a, ...b])],
})

// Instant, synchronous � the cached set, for the first render before Drive answers.
export function cachedDismissedNews(): Set<string> {
  return new Set(dismissed.cachedNow())
}

// The current dismissed set from the Drive sidecar, with the local cache folded
// in (so a cache-only install seeds it). Any failure falls back to the cache.
export async function dismissedNewsSynced(
  token: string,
  libraryFolderId: string,
): Promise<Set<string>> {
  return new Set(await dismissed.sync(token, libraryFolderId))
}

// Add `link` to the sidecar (read -> union with the server copy + `current` ->
// write). Best-effort: returns the new set even if the Drive write fails.
export async function dismissNewsItem(
  token: string,
  libraryFolderId: string,
  link: string,
  current: Set<string>,
): Promise<Set<string>> {
  const next = await dismissed.write(token, libraryFolderId, (cur) => [
    ...new Set([...cur, ...current, link]),
  ])
  return new Set(next)
}

// Drop dismissed links no longer in any feed � once an article has aged out
// everywhere it can't come back, so the list needn't grow forever. Writes only
// when something actually changed.
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
  const written = await dismissed.write(token, libraryFolderId, (cur) =>
    cur.filter((l) => live.has(l)),
  )
  return new Set(written)
}

export async function fetchNews(token: string, libraryFolderId: string): Promise<News> {
  const cached = readCache()
  const cacheValid = cached?.libraryFolderId === libraryFolderId
  try {
    const meta = await findSidecarMeta(token, libraryFolderId, FILENAME)
    if (!meta) return cacheValid ? cached.news : EMPTY_NEWS
    if (cacheValid && cached.modifiedTime === meta.modifiedTime) return cached.news

    const buf = await fetchDriveBytes(token, meta.id)
    const news = normaliseNews(JSON.parse(new TextDecoder().decode(buf)) as RawFile)
    try {
      localStorage.setItem(
        CACHE_KEY,
        JSON.stringify({ libraryFolderId, modifiedTime: meta.modifiedTime, news }),
      )
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
