import { describe, expect, it } from 'vitest'
import { dedupeReleaseItems, monthYear, seriesEntryToItem, type ReleaseItem } from './releases'
import type { SeriesReleaseEntry } from './seriesGaps'

const entry = (over: Partial<SeriesReleaseEntry> = {}): SeriesReleaseEntry => ({
  seriesName: 'Mistborn',
  hardcoverSlug: 'mistborn',
  position: 4,
  title: 'The Alloy of Law',
  releaseDate: '2011-11-08',
  isbn13: '9780765330420',
  ...over,
})

const item = (over: Partial<ReleaseItem> = {}): ReleaseItem => ({
  key: 'k',
  title: 'T',
  author: null,
  series: null,
  seriesPosition: null,
  isbn13: null,
  releaseDate: null,
  description: null,
  hardcoverSlug: null,
  genres: [],
  source: 'author',
  ...over,
})

describe('seriesEntryToItem', () => {
  it('carries the series identity and keys on the ISBN', () => {
    expect(seriesEntryToItem(entry())).toMatchObject({
      key: '9780765330420',
      title: 'The Alloy of Law',
      series: 'Mistborn',
      seriesPosition: 4,
      hardcoverSlug: 'mistborn',
      source: 'series',
    })
  })

  it('falls back to a synthetic key with no ISBN', () => {
    expect(seriesEntryToItem(entry({ isbn13: null })).key).toBe('series:Mistborn#4')
  })
})

describe('dedupeReleaseItems', () => {
  it('prefers earlier lists and drops a later duplicate by ISBN', () => {
    const out = dedupeReleaseItems(
      [item({ key: 'a', title: 'Book', isbn13: '111', source: 'series' })],
      [item({ key: 'b', title: 'Book (UK)', isbn13: '111', source: 'author' })],
    )
    expect(out).toHaveLength(1)
    expect(out[0].source).toBe('series')
  })

  it('dedupes by normalised title when there is no ISBN', () => {
    const out = dedupeReleaseItems([
      item({ key: 'a', title: 'The Way of Kings' }),
      item({ key: 'b', title: 'the-way-of-kings!' }),
    ])
    expect(out).toHaveLength(1)
  })
})

describe('monthYear', () => {
  it('formats or returns null', () => {
    expect(monthYear('2024-12-01')).toMatch(/2024/)
    expect(monthYear(null)).toBeNull()
    expect(monthYear('not-a-date')).toBeNull()
  })
})
