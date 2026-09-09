// bookbrain-news.json — the latest articles from a curated set of SFF news /
// review feeds, fetched + parsed by the backend (the static viewer can't:
// none of the feeds send CORS headers). prompts/32, written by
// library_index_service.regenerate_news.
//
// Same lazy modifiedTime-gated localStorage cache as newReleases.ts. Every
// item links out to the original — we only ever store headline + a short
// excerpt + attribution.

const FILENAME = 'bookbrain-news.json'
const CACHE_KEY = 'bookbrain.news'

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
