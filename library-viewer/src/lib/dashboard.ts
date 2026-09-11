// bookbrain-dashboard.json — a snapshot of the OpenBooks acquisition
// pipeline (queue size, up next, recent downloads, hit rate), written by the
// backend (library_index_service.regenerate_dashboard): refreshed nightly
// and after every auto-get download. The static viewer can't see the
// backend's SQLite acquisition_candidates table any other way — this is the
// one Dashboard section that isn't computed client-side.
//
// Read-only from here — same modifiedTime-gated localStorage cache as
// news.ts / newReleases.ts.

const FILENAME = 'bookbrain-dashboard.json'
const CACHE_KEY = 'bookbrain.dashboard'

export interface DashboardQueue {
  wanted: number
  pending: number
  noMatch: number
  failed: number
  approvedAllTime: number
}

export interface DashboardPick {
  title: string
  author: string | null
  server: string | null
  score: number | null
}

export interface DashboardDownload {
  title: string
  author: string | null
  server: string | null
  at: string
}

export interface DashboardHitRate {
  approved: number
  noMatch: number
  pct: number | null
}

export interface Dashboard {
  generatedAt: string | null
  queue: DashboardQueue
  upNext: DashboardPick[]
  recentDownloads: DashboardDownload[]
  hitRate: DashboardHitRate
  downloadsLast24h: number
  downloadsLast7d: number
  avgPerDay7d: number
  etaDays: number | null
}

export const EMPTY_DASHBOARD: Dashboard = {
  generatedAt: null,
  queue: { wanted: 0, pending: 0, noMatch: 0, failed: 0, approvedAllTime: 0 },
  upNext: [],
  recentDownloads: [],
  hitRate: { approved: 0, noMatch: 0, pct: null },
  downloadsLast24h: 0,
  downloadsLast7d: 0,
  avgPerDay7d: 0,
  etaDays: null,
}

interface RawPick {
  title?: unknown
  author?: unknown
  server?: unknown
  score?: unknown
}
interface RawDownload {
  title?: unknown
  author?: unknown
  server?: unknown
  at?: unknown
}
interface RawFile {
  version?: number
  generatedAt?: string
  queue?: Partial<DashboardQueue>
  upNext?: RawPick[]
  recentDownloads?: RawDownload[]
  hitRate?: Partial<DashboardHitRate>
  downloadsLast24h?: number
  downloadsLast7d?: number
  avgPerDay7d?: number
  etaDays?: number | null
}

interface Cached {
  libraryFolderId: string
  modifiedTime: string | null
  dashboard: Dashboard
}

function num(v: unknown, fallback = 0): number {
  return typeof v === 'number' && Number.isFinite(v) ? v : fallback
}

export function normaliseDashboard(raw: RawFile): Dashboard {
  const upNext: DashboardPick[] = (raw.upNext ?? [])
    .filter((p): p is RawPick => !!p && typeof p.title === 'string')
    .map((p) => ({
      title: p.title as string,
      author: typeof p.author === 'string' ? p.author : null,
      server: typeof p.server === 'string' ? p.server : null,
      score: typeof p.score === 'number' ? p.score : null,
    }))
  const recentDownloads: DashboardDownload[] = (raw.recentDownloads ?? [])
    .filter(
      (d): d is RawDownload => !!d && typeof d.title === 'string' && typeof d.at === 'string',
    )
    .map((d) => ({
      title: d.title as string,
      author: typeof d.author === 'string' ? d.author : null,
      server: typeof d.server === 'string' ? d.server : null,
      at: d.at as string,
    }))
  return {
    generatedAt: typeof raw.generatedAt === 'string' ? raw.generatedAt : null,
    queue: {
      wanted: num(raw.queue?.wanted),
      pending: num(raw.queue?.pending),
      noMatch: num(raw.queue?.noMatch),
      failed: num(raw.queue?.failed),
      approvedAllTime: num(raw.queue?.approvedAllTime),
    },
    upNext,
    recentDownloads,
    hitRate: {
      approved: num(raw.hitRate?.approved),
      noMatch: num(raw.hitRate?.noMatch),
      pct: typeof raw.hitRate?.pct === 'number' ? raw.hitRate.pct : null,
    },
    downloadsLast24h: num(raw.downloadsLast24h),
    downloadsLast7d: num(raw.downloadsLast7d),
    avgPerDay7d: num(raw.avgPerDay7d),
    etaDays: typeof raw.etaDays === 'number' ? raw.etaDays : null,
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

export async function fetchDashboard(token: string, libraryFolderId: string): Promise<Dashboard> {
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
    if (files.length === 0) return cacheValid ? cached.dashboard : EMPTY_DASHBOARD

    const { id, modifiedTime } = files[0]
    if (cacheValid && cached.modifiedTime === modifiedTime) return cached.dashboard

    const fileResp = await fetch(`https://www.googleapis.com/drive/v3/files/${id}?alt=media`, {
      headers: { Authorization: `Bearer ${token}` },
    })
    if (!fileResp.ok) throw new Error(`download ${fileResp.status}`)
    const dashboard = normaliseDashboard((await fileResp.json()) as RawFile)
    try {
      localStorage.setItem(CACHE_KEY, JSON.stringify({ libraryFolderId, modifiedTime, dashboard }))
    } catch {
      /* over quota / private mode */
    }
    return dashboard
  } catch {
    return cacheValid ? cached.dashboard : EMPTY_DASHBOARD
  }
}
