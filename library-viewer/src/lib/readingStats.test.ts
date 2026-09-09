import { describe, expect, it } from 'vitest'
import type { BookRow } from './books'
import { EMPTY_READING, type Reading } from './reading'
import { readingStats } from './readingStats'

function row(id: string, over: Partial<BookRow> = {}): BookRow {
  return {
    id,
    file: { id, name: `${id}.epub` },
    filename: `${id}.epub`,
    title: id,
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

const reading: Reading = {
  ...EMPTY_READING,
  reader: 'James',
  books: {
    a: { status: 'read', rating: 5, readDate: '2026-07-10' },
    b: { status: 'read', rating: 4, readDate: '2026-06-02' },
    c: { status: 'read', rating: 4.5, readDate: '2025-11-01' }, // last year
    d: { status: 'want' },
    e: { status: 'read', readDate: '2026-07-28' }, // no rating
  },
}

const rows = [
  row('a', { author: 'Sanderson', series: 'Stormlight', meta: metaPages(1200) }),
  row('b', { author: 'Sanderson', meta: metaPages(400) }),
  row('c', { author: 'Le Guin', meta: metaPages(300) }),
  row('e', { author: 'Sanderson', series: 'Stormlight', meta: metaPages(1100) }),
]

function metaPages(pages: number): BookRow['meta'] {
  return {
    rating: null,
    ratingsCount: null,
    pages,
    category: null,
    literaryType: null,
    genres: [],
    moods: [],
    contentWarnings: [],
    listsCount: null,
  }
}

describe('readingStats', () => {
  const stats = readingStats(reading, rows, new Date('2026-07-30T00:00:00Z'))

  it('counts reads by year and all-time', () => {
    expect(stats.readAllTime).toBe(4) // a, b, c, e
    expect(stats.readThisYear).toBe(3) // a, b, e (c was 2025)
  })

  it('sums pages read this year and finds the longest', () => {
    expect(stats.pagesThisYear).toBe(1200 + 400 + 1100)
    expect(stats.longestRead).toEqual({ title: 'a', pages: 1200 })
  })

  it('this month vs last month', () => {
    expect(stats.thisMonth).toBe(2) // a (Jul 10), e (Jul 28)
    expect(stats.lastMonth).toBe(1) // b (Jun 2)
  })

  it('rating distribution and average (ceil to a star bucket)', () => {
    expect(stats.avgRating).toBe(4.5) // (5 + 4 + 4.5) / 3
    expect(stats.ratingCounts.find((r) => r.rating === 5)?.count).toBe(2) // 5 and 4.5 → bucket 5
    expect(stats.ratingCounts.find((r) => r.rating === 4)?.count).toBe(1)
  })

  it('top authors and series by read count', () => {
    expect(stats.topAuthors[0]).toEqual({ name: 'Sanderson', count: 3 })
    expect(stats.topSeries[0]).toEqual({ name: 'Stormlight', count: 2 })
  })
})
