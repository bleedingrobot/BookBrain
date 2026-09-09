// The "New & Upcoming" feed the release strips + <NewReleasesScreen> read
// from. prompts/27.
//
// Part 1 fills it entirely from data already in bookbrain-index.json — the
// per-series Hardcover catalogues (see collectSeriesReleases in seriesGaps.ts).
// Part 2 adds a backend sidecar (bookbrain-new-releases.json) covering the
// authors James reads, merged in here so the viewer has one feed.

import type { SeriesReleaseEntry } from './seriesGaps'

export interface ReleaseItem {
  // Stable identity for React keys + cover dedup: the ISBN-13 when we have
  // one, else a synthetic series+position / title+author key.
  key: string
  title: string
  author: string | null
  series: string | null
  seriesPosition: number | null
  isbn13: string | null
  // ISO date, or null for a "recent" entry we can't date precisely.
  releaseDate: string | null
  description: string | null
  hardcoverSlug: string | null
  genres: string[]
  source: 'series' | 'author' | 'global' | 'trending'
}

// Same-book identity for dedup: ISBN-13 when present, else normalised title.
export function releaseDedupeKey(item: ReleaseItem): string {
  return item.isbn13
    ? `isbn:${item.isbn13}`
    : `t:${item.title.toLowerCase().replace(/[^a-z0-9]+/g, '')}`
}

export function seriesEntryToItem(entry: SeriesReleaseEntry): ReleaseItem {
  return {
    key: entry.isbn13 || `series:${entry.seriesName}#${entry.position}`,
    title: entry.title,
    author: null,
    series: entry.seriesName,
    seriesPosition: entry.position,
    isbn13: entry.isbn13,
    releaseDate: entry.releaseDate,
    description: null,
    hardcoverSlug: entry.hardcoverSlug,
    genres: [],
    source: 'series',
  }
}

// Concatenate feeds (series-derived first, then author-sidecar) and drop a
// later entry that's the same book as an earlier one — keyed by ISBN-13 when
// both have one, else normalised title. Keeps the list stably ordered:
// callers sort by date themselves.
export function dedupeReleaseItems(...lists: ReleaseItem[][]): ReleaseItem[] {
  const seen = new Set<string>()
  const out: ReleaseItem[] = []
  for (const item of lists.flat()) {
    const key = releaseDedupeKey(item)
    if (seen.has(key)) continue
    seen.add(key)
    out.push(item)
  }
  return out
}

// "December 2024" from an ISO date, best-effort (shared with BookRow's copy).
export function monthYear(iso: string | null): string | null {
  if (!iso) return null
  const d = new Date(iso)
  return Number.isNaN(d.getTime())
    ? null
    : d.toLocaleDateString(undefined, { month: 'long', year: 'numeric' })
}
