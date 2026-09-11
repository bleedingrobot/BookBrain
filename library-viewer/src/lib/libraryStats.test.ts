import { describe, expect, it } from 'vitest'
import type { BookRow } from './books'
import {
  addedSince,
  avgPages,
  distinctCount,
  growthHistogram,
  newlyCompletedSeries,
  topOwnedSeries,
} from './libraryStats'
import type { SeriesGap } from './seriesGaps'

function row(over: Partial<BookRow> = {}): BookRow {
  return {
    id: 'id',
    file: { id: 'id', name: 'f.epub' },
    filename: 'f.epub',
    title: 'Title',
    author: null,
    series: null,
    seriesNumber: null,
    description: null,
    addedAt: null,
    isbn: null,
    meta: null,
    reading: null,
    ...over,
  }
}

describe('addedSince', () => {
  it('counts only rows added within the window', () => {
    const now = Date.now()
    const rows = [
      row({ id: '1', addedAt: new Date(now - 1000).toISOString() }), // 1s ago
      row({ id: '2', addedAt: new Date(now - 48 * 3_600_000).toISOString() }), // 48h ago
      row({ id: '3', addedAt: null }),
    ]
    expect(addedSince(rows, 24)).toBe(1)
    expect(addedSince(rows, 72)).toBe(2)
  })
})

describe('growthHistogram', () => {
  it('buckets by day and keeps zero days present', () => {
    const today = new Date().toISOString().slice(0, 10)
    const rows = [row({ addedAt: `${today}T10:00:00Z` }), row({ addedAt: `${today}T11:00:00Z` })]
    const hist = growthHistogram(rows, 3)
    expect(hist).toHaveLength(3)
    expect(hist[hist.length - 1]).toEqual({ date: today, count: 2 })
    expect(hist[0].count).toBe(0)
  })
})

describe('distinctCount / avgPages / topOwnedSeries', () => {
  it('tallies authors, series, page counts', () => {
    const rows = [
      row({ id: '1', author: 'A', series: 'S1', meta: { ...emptyMeta, pages: 200 } }),
      row({ id: '2', author: 'A', series: 'S1', meta: { ...emptyMeta, pages: 400 } }),
      row({ id: '3', author: 'B', series: 'S2', meta: null }),
    ]
    expect(distinctCount(rows, 'author')).toBe(2)
    expect(distinctCount(rows, 'series')).toBe(2)
    expect(avgPages(rows)).toBe(300)
    expect(topOwnedSeries(rows, 1)).toEqual([{ name: 'S1', count: 2 }])
  })

  it('avgPages is null with no page data', () => {
    expect(avgPages([row()])).toBeNull()
  })
})

const emptyMeta = {
  rating: null,
  ratingsCount: null,
  pages: null,
  category: null,
  literaryType: null,
  genres: [],
  moods: [],
  contentWarnings: [],
  listsCount: null,
  published: null,
  audioHours: null,
}

describe('newlyCompletedSeries', () => {
  it('only reports a hardcover-sourced series with no gap and no further release', () => {
    const rows = [
      row({ id: '1', series: 'Complete', author: 'A', addedAt: '2026-01-01T00:00:00Z' }),
      row({ id: '2', series: 'Complete', author: 'A', addedAt: '2026-02-01T00:00:00Z' }),
      row({ id: '3', series: 'HasGap', author: 'B', addedAt: '2026-03-01T00:00:00Z' }),
      row({ id: '4', series: 'Guessed', author: 'C', addedAt: '2026-04-01T00:00:00Z' }),
    ]
    const gaps = new Map<string, SeriesGap>([
      ['Complete', { have: [1, 2], missing: [], source: 'hardcover', nextUp: null }],
      ['HasGap', { have: [1], missing: [2], source: 'hardcover' }],
      ['Guessed', { have: [1], missing: [], source: 'guess' }],
    ])

    const out = newlyCompletedSeries(rows, gaps)
    expect(out).toHaveLength(1)
    expect(out[0]).toMatchObject({ name: 'Complete', author: 'A', bookCount: 2, latestBookId: '2' })
  })

  it('excludes a series with an already-released book still missing', () => {
    const rows = [row({ id: '1', series: 'AlmostThere', addedAt: '2026-01-01T00:00:00Z' })]
    const gaps = new Map<string, SeriesGap>([
      [
        'AlmostThere',
        {
          have: [1],
          missing: [],
          source: 'hardcover',
          nextUp: { seriesName: 'AlmostThere', hardcoverSlug: null, position: 2, title: 'Book 2', releaseDate: null, isbn13: null },
        },
      ],
    ])
    expect(newlyCompletedSeries(rows, gaps)).toHaveLength(0)
  })

  it('orders most-recently-completed first and respects the limit', () => {
    const rows = [
      row({ id: '1', series: 'Older', addedAt: '2026-01-01T00:00:00Z' }),
      row({ id: '2', series: 'Newer', addedAt: '2026-06-01T00:00:00Z' }),
    ]
    const gaps = new Map<string, SeriesGap>([
      ['Older', { have: [1], missing: [], source: 'hardcover', nextUp: null }],
      ['Newer', { have: [1], missing: [], source: 'hardcover', nextUp: null }],
    ])
    expect(newlyCompletedSeries(rows, gaps, 1).map((s) => s.name)).toEqual(['Newer'])
  })
})
