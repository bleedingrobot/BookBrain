import { describe, expect, it } from 'vitest'
import {
  buildRows,
  groupHeading,
  matchesFilter,
  matchesRow,
  SORTS,
  type BookRow,
} from './books'
import { EMPTY_INDEX, type LibraryIndex } from './libraryIndex'
import type { SentMap } from './sentTracker'

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
    ...over,
  }
}

describe('buildRows', () => {
  it('prefers index metadata, falls back to the filename', () => {
    const files = [
      { id: '1', name: 'Frank Herbert, Dune, Dune Chronicles, 1.epub' },
      { id: '2', name: 'Someone, Other Book.epub' },
    ]
    const index: LibraryIndex = {
      coversFolder: null,
      entries: {
        '1': {
          title: 'Dune (Deluxe)',
          author: 'Frank Herbert',
          series: 'Dune',
          seriesNumber: 1,
          description: 'x',
          addedAt: '2026-01-01',
          isbn: '9780441172719',
        },
      },
    }
    const rows = buildRows(files, index)
    expect(rows[0]).toMatchObject({ title: 'Dune (Deluxe)', series: 'Dune', seriesNumber: '1', isbn: '9780441172719' })
    expect(rows[1]).toMatchObject({ title: 'Other Book', author: 'Someone', isbn: null })
  })

  it('handles an empty index', () => {
    const rows = buildRows([{ id: '1', name: 'A, B.epub' }], EMPTY_INDEX)
    expect(rows[0].title).toBe('B')
  })
})

describe('matchesRow', () => {
  it('empty query matches everything', () => {
    expect(matchesRow(row(), '')).toBe(true)
  })
  it('AND-matches every term across title/author/series/filename', () => {
    const r = row({ title: 'The Way of Kings', author: 'Brandon Sanderson', series: 'Stormlight' })
    expect(matchesRow(r, 'kings sander')).toBe(true)
    expect(matchesRow(r, 'kings mistborn')).toBe(false)
  })
})

describe('matchesFilter', () => {
  const sent: SentMap = { james: { a: 't' }, tess: { b: 't' } }
  const incomplete = new Set(['Mistborn'])
  const m = (r: BookRow, f: Parameters<typeof matchesFilter>[1]) =>
    matchesFilter(r, f, sent, incomplete)
  it('all', () => expect(m(row({ id: 'a' }), 'all')).toBe(true))
  it('noseries', () => {
    expect(m(row({ series: null }), 'noseries')).toBe(true)
    expect(m(row({ series: 'X' }), 'noseries')).toBe(false)
  })
  it('gaps = in an incomplete series', () => {
    expect(m(row({ series: 'Mistborn' }), 'gaps')).toBe(true)
    expect(m(row({ series: 'Elantris' }), 'gaps')).toBe(false)
    expect(m(row({ series: null }), 'gaps')).toBe(false)
  })
  it('on:<folder>', () => {
    expect(m(row({ id: 'a' }), 'on:james')).toBe(true)
    expect(m(row({ id: 'b' }), 'on:james')).toBe(false)
  })
  it('off:<folder>', () => {
    expect(m(row({ id: 'b' }), 'off:james')).toBe(true)
    expect(m(row({ id: 'a' }), 'off:james')).toBe(false)
  })
  it('unsent = on no device at all', () => {
    expect(m(row({ id: 'c' }), 'unsent')).toBe(true)
    expect(m(row({ id: 'a' }), 'unsent')).toBe(false)
  })
})

describe('SORTS', () => {
  it('title sorts alphabetically', () => {
    const rows = [row({ title: 'Beta' }), row({ title: 'alpha' })].sort(SORTS.title)
    expect(rows.map((r) => r.title)).toEqual(['alpha', 'Beta'])
  })

  it('series sorts by series, then number, then title; unknowns last', () => {
    const rows = [
      row({ title: 'Z', series: null }),
      row({ title: 'A2', series: 'Mistborn', seriesNumber: '2' }),
      row({ title: 'A1', series: 'Mistborn', seriesNumber: '1' }),
      row({ title: 'B', series: 'Elantris', seriesNumber: null }),
    ].sort(SORTS.series)
    expect(rows.map((r) => r.title)).toEqual(['B', 'A1', 'A2', 'Z'])
  })

  it('added sorts newest first, undated last', () => {
    const rows = [
      row({ title: 'old', addedAt: '2026-01-01' }),
      row({ title: 'none', addedAt: null }),
      row({ title: 'new', addedAt: '2026-06-01' }),
    ].sort(SORTS.added)
    expect(rows.map((r) => r.title)).toEqual(['new', 'old', 'none'])
  })
})

describe('groupHeading', () => {
  it('only returns a heading for the matching name sort', () => {
    const r = row({ author: 'Sanderson', series: 'Mistborn' })
    expect(groupHeading(r, 'author')).toBe('Sanderson')
    expect(groupHeading(r, 'series')).toBe('Mistborn')
    expect(groupHeading(r, 'title')).toBeNull()
    expect(groupHeading(row({ author: null }), 'author')).toBeNull()
  })
})
