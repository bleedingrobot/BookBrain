// Fun/interesting numbers about the library itself, computed entirely from
// data the viewer already holds (BookRow[] + series gaps) — no network call
// of its own, same contract as readingStats.ts. Feeds the Dashboard screen's
// "library growth" and "fun facts" sections; the acquisition-pipeline
// numbers (queue, up next, hit rate) live in lib/dashboard.ts instead, since
// those come from the backend.

import type { BookRow } from './books'
import type { SeriesGap } from './seriesGaps'

export function addedSince(rows: BookRow[], hours: number): number {
  const cutoff = Date.now() - hours * 3_600_000
  return rows.filter((r) => r.addedAt && new Date(r.addedAt).getTime() >= cutoff).length
}

export interface GrowthDay {
  date: string // yyyy-mm-dd
  count: number
}

// One bucket per day for the last `days` days (today inclusive), oldest
// first — a ready-to-plot sparkline of library growth.
export function growthHistogram(rows: BookRow[], days = 14): GrowthDay[] {
  const buckets = new Map<string, number>()
  const today = new Date()
  for (let i = days - 1; i >= 0; i--) {
    const d = new Date(today)
    d.setDate(d.getDate() - i)
    buckets.set(d.toISOString().slice(0, 10), 0)
  }
  for (const r of rows) {
    if (!r.addedAt) continue
    const key = r.addedAt.slice(0, 10)
    if (buckets.has(key)) buckets.set(key, (buckets.get(key) ?? 0) + 1)
  }
  return [...buckets.entries()].map(([date, count]) => ({ date, count }))
}

export interface CompletedSeries {
  name: string
  author: string | null
  bookCount: number
  completedAt: string | null
  // The most-recently-added member — presumably the one that completed it —
  // so the card can show its cover.
  latestBookId: string | null
  latestBookIsbn: string | null
}

// A series where you own every released entry Hardcover knows about (no
// gaps, nothing already-out still missing), ordered by the addedAt of its
// most-recently-added member — a reasonable stand-in for "when it became
// complete" without a dedicated timestamp anywhere.
export function newlyCompletedSeries(
  rows: BookRow[],
  gaps: Map<string, SeriesGap>,
  limit = 3,
): CompletedSeries[] {
  const bySeries = new Map<
    string,
    { author: string | null; count: number; latest: string | null; latestId: string | null; latestIsbn: string | null }
  >()
  for (const row of rows) {
    if (!row.series) continue
    const cur =
      bySeries.get(row.series) ??
      { author: row.author, count: 0, latest: null, latestId: null, latestIsbn: null }
    cur.count += 1
    if (!cur.author && row.author) cur.author = row.author
    if (row.addedAt && (!cur.latest || row.addedAt > cur.latest)) {
      cur.latest = row.addedAt
      cur.latestId = row.id
      cur.latestIsbn = row.isbn
    }
    bySeries.set(row.series, cur)
  }

  const out: CompletedSeries[] = []
  for (const [name, gap] of gaps) {
    if (gap.source !== 'hardcover') continue
    if (gap.missing.length > 0) continue
    if (gap.nextUp) continue // a released book you don't own yet — not actually complete
    const info = bySeries.get(name)
    if (!info) continue
    out.push({
      name,
      author: info.author,
      bookCount: info.count,
      completedAt: info.latest,
      latestBookId: info.latestId,
      latestBookIsbn: info.latestIsbn,
    })
  }
  out.sort((a, b) => (b.completedAt ?? '').localeCompare(a.completedAt ?? ''))
  return out.slice(0, limit)
}

export function distinctCount(rows: BookRow[], key: 'author' | 'series'): number {
  return new Set(rows.map((r) => r[key]).filter((v): v is string => !!v)).size
}

export function avgPages(rows: BookRow[]): number | null {
  const pages = rows.map((r) => r.meta?.pages).filter((p): p is number => typeof p === 'number')
  if (pages.length === 0) return null
  return Math.round(pages.reduce((a, b) => a + b, 0) / pages.length)
}

export interface NamedCount {
  name: string
  count: number
}

// Series with the most owned entries — "longest series you own", not a
// reading stat (see readingStats.ts's topSeries for that).
export function topOwnedSeries(rows: BookRow[], limit = 1): NamedCount[] {
  const counts = new Map<string, number>()
  for (const r of rows) {
    if (!r.series) continue
    counts.set(r.series, (counts.get(r.series) ?? 0) + 1)
  }
  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .slice(0, limit)
    .map(([name, count]) => ({ name, count }))
}
