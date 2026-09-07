import { describe, expect, it } from 'vitest'
import { clearCachedIndex, EMPTY_INDEX, loadCachedIndex, normalise } from './libraryIndex'

describe('normalise', () => {
  it('keeps well-formed entries and carries coversFolder', () => {
    const out = normalise({
      coversFolder: 'cov-1',
      books: {
        a: {
          title: 'A',
          author: 'Auth',
          series: 'S',
          seriesNumber: 3,
          description: 'd',
          addedAt: '2026-01-01',
          isbn: '123',
        },
      },
    })
    expect(out.coversFolder).toBe('cov-1')
    expect(out.entries.a).toEqual({
      title: 'A',
      author: 'Auth',
      series: 'S',
      seriesNumber: 3,
      description: 'd',
      addedAt: '2026-01-01',
      isbn: '123',
    })
  })

  it('drops entries with no title and coerces missing fields to null', () => {
    const out = normalise({
      books: {
        good: { title: 'T' },
        bad: { author: 'no title' } as { author: string },
      },
    })
    expect(Object.keys(out.entries)).toEqual(['good'])
    expect(out.entries.good).toMatchObject({ author: null, series: null, seriesNumber: null, isbn: null })
    expect(out.coversFolder).toBeNull()
  })

  it('ignores a non-number seriesNumber and non-string isbn', () => {
    const out = normalise({
      books: { a: { title: 'A', seriesNumber: '2' as unknown as number, isbn: 5 as unknown as string } },
    })
    expect(out.entries.a.seriesNumber).toBeNull()
    expect(out.entries.a.isbn).toBeNull()
  })
})

describe('loadCachedIndex', () => {
  it('is EMPTY_INDEX with no cache', () => {
    expect(loadCachedIndex('lib-1')).toBe(EMPTY_INDEX)
  })

  it('returns the cached index only for a matching library folder', () => {
    const index = { entries: { a: { title: 'A' } }, coversFolder: null }
    localStorage.setItem(
      'bookbrain.metadataIndex',
      JSON.stringify({ libraryFolderId: 'lib-1', modifiedTime: 't', index }),
    )
    expect(loadCachedIndex('lib-1')).toEqual(index)
    expect(loadCachedIndex('other')).toBe(EMPTY_INDEX)
  })

  it('rejects the pre-restructure cache shape', () => {
    localStorage.setItem(
      'bookbrain.metadataIndex',
      JSON.stringify({ libraryFolderId: 'lib-1', modifiedTime: 't', index: { a: { title: 'A' } } }),
    )
    expect(loadCachedIndex('lib-1')).toBe(EMPTY_INDEX)
  })

  it('clearCachedIndex removes it', () => {
    localStorage.setItem('bookbrain.metadataIndex', '{}')
    clearCachedIndex()
    expect(localStorage.getItem('bookbrain.metadataIndex')).toBeNull()
  })
})
