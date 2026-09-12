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
      meta: null,
    })
  })

  it('parses per-book meta, coercing bad fields and dropping empty meta', () => {
    const out = normalise({
      books: {
        full: {
          title: 'Full',
          meta: {
            rating: 4.4,
            ratingsCount: 12,
            pages: 500,
            category: 'Novella',
            literaryType: 'Fiction',
            genres: ['Fantasy', 5 as unknown as string],
            moods: ['dark'],
            contentWarnings: ['Violence', 7 as unknown as string],
            listsCount: 3223,
          },
        },
        bad: { title: 'Bad', meta: { rating: 'nope' as unknown as number, genres: 'x' as unknown as string[] } },
      },
    })
    expect(out.entries.full.meta).toEqual({
      rating: 4.4,
      ratingsCount: 12,
      pages: 500,
      category: 'Novella',
      literaryType: 'Fiction',
      genres: ['Fantasy'],
      moods: ['dark'],
      contentWarnings: ['Violence'],
      listsCount: 3223,
      published: null,
      audioHours: null,
    })
    expect(out.entries.bad.meta).toBeNull()
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

describe('normalise — series catalog', () => {
  it('keeps release dates + isbn13 and coerces missing ones to null', () => {
    const out = normalise({
      series: {
        Mistborn: {
          hardcoverId: 5,
          hardcoverName: 'The Mistborn Saga',
          hardcoverSlug: 'the-mistborn-saga',
          primaryCount: 3,
          books: [
            {
              position: 1,
              title: 'The Final Empire',
              releaseDate: '2006-07-17',
              isbn13: '9780765311788',
            },
            { position: 2, title: 'The Well of Ascension' },
            { position: 3, title: 5 as unknown as string }, // dropped
          ],
        },
      },
    })
    expect(out.series.Mistborn.books).toEqual([
      {
        position: 1,
        title: 'The Final Empire',
        releaseDate: '2006-07-17',
        isbn13: '9780765311788',
      },
      { position: 2, title: 'The Well of Ascension', releaseDate: null, isbn13: null },
    ])
  })
})

describe('loadCachedIndex', () => {
  it('is EMPTY_INDEX with no cache', () => {
    expect(loadCachedIndex('lib-1')).toBe(EMPTY_INDEX)
  })

  it('returns the cached index only for a matching library folder', () => {
    const index = { entries: { a: { title: 'A' } }, series: {}, coversFolder: null }
    localStorage.setItem(
      'bookbrain.metadataIndex',
      JSON.stringify({ libraryFolderId: 'lib-1', modifiedTime: 't', index }),
    )
    expect(loadCachedIndex('lib-1')).toEqual(index)
    expect(loadCachedIndex('other')).toBe(EMPTY_INDEX)
  })

  it('back-fills series:{} into a pre-v3 cached index', () => {
    localStorage.setItem(
      'bookbrain.metadataIndex',
      JSON.stringify({
        libraryFolderId: 'lib-1',
        modifiedTime: 't',
        index: { entries: { a: { title: 'A' } }, coversFolder: null },
      }),
    )
    expect(loadCachedIndex('lib-1').series).toEqual({})
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
