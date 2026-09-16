// prompts/31 Part C — a reading-stats summary derived entirely from the
// reading sidecar joined to the library rows. No network call: everything
// here comes from data App already holds.
//
// These stats only see books that are BOTH in this library AND on the
// owner's Hardcover shelves — the sidecar doesn't carry books they've read
// but don't own (licence). The goal line (reading.goal) is the whole-shelf
// number; the stats screen labels itself "from your library" to be honest
// about the difference.

import type { BookRow } from './books'
import type { Reading } from './reading'

export interface ReadingStats {
  readThisYear: number
  readAllTime: number
  pagesThisYear: number
  thisMonth: number
  lastMonth: number
  avgRating: number | null
  ratingCounts: { rating: number; count: number }[] // 1..5 (halves merged up)
  topAuthors: { name: string; count: number }[]
  topSeries: { name: string; count: number }[]
  longestRead: { title: string; pages: number } | null
}

const EMPTY: ReadingStats = {
  readThisYear: 0,
  readAllTime: 0,
  pagesThisYear: 0,
  thisMonth: 0,
  lastMonth: 0,
  avgRating: null,
  ratingCounts: [],
  topAuthors: [],
  topSeries: [],
  longestRead: null,
}

function topN(counts: Map<string, number>, n: number): { name: string; count: number }[] {
  return [...counts.entries()]
    .filter(([name]) => name)
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .slice(0, n)
    .map(([name, count]) => ({ name, count }))
}

export function readingStats(reading: Reading, rows: BookRow[], now = new Date()): ReadingStats {
  const byId = new Map(rows.map((r) => [r.id, r]))
  const year = now.getUTCFullYear()
  const thisMonthKey = `${year}-${String(now.getUTCMonth() + 1).padStart(2, '0')}`
  const lastMonthDate = new Date(Date.UTC(year, now.getUTCMonth() - 1, 1))
  const lastMonthKey = `${lastMonthDate.getUTCFullYear()}-${String(
    lastMonthDate.getUTCMonth() + 1,
  ).padStart(2, '0')}`

  const out: ReadingStats = { ...EMPTY, ratingCounts: [], topAuthors: [], topSeries: [] }
  const authors = new Map<string, number>()
  const series = new Map<string, number>()
  const ratingBuckets = new Map<number, number>()
  let ratingSum = 0
  let ratingN = 0

  for (const [id, entry] of Object.entries(reading.books)) {
    if (typeof entry.rating === 'number') {
      const bucket = Math.max(1, Math.min(5, Math.ceil(entry.rating)))
      ratingBuckets.set(bucket, (ratingBuckets.get(bucket) ?? 0) + 1)
      ratingSum += entry.rating
      ratingN += 1
    }
    if (entry.status !== 'read') continue
    out.readAllTime += 1

    const row = byId.get(id)
    if (row?.author) authors.set(row.author, (authors.get(row.author) ?? 0) + 1)
    if (row?.series) series.set(row.series, (series.get(row.series) ?? 0) + 1)

    const d = entry.readDate ?? ''
    if (d.slice(0, 4) === String(year)) {
      out.readThisYear += 1
      const pages = row?.meta?.pages
      if (typeof pages === 'number' && pages > 0) {
        out.pagesThisYear += pages
        if (!out.longestRead || pages > out.longestRead.pages) {
          out.longestRead = { title: row!.title, pages }
        }
      }
    }
    if (d.slice(0, 7) === thisMonthKey) out.thisMonth += 1
    if (d.slice(0, 7) === lastMonthKey) out.lastMonth += 1
  }

  out.avgRating = ratingN > 0 ? Math.round((ratingSum / ratingN) * 10) / 10 : null
  out.ratingCounts = [1, 2, 3, 4, 5].map((rating) => ({
    rating,
    count: ratingBuckets.get(rating) ?? 0,
  }))
  out.topAuthors = topN(authors, 5)
  out.topSeries = topN(series, 5)
  return out
}
