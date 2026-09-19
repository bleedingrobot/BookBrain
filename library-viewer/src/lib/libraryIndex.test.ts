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
      llmTags: null,
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

  it('parses per-book llmTags, coercing bad fields, and requires a shortDescription', () => {
    const out = normalise({
      books: {
        full: {
          title: 'Full',
          llmTags: {
            ageRating: 'Teen',
            genres: ['Fantasy', 5 as unknown as string],
            moods: ['tense'],
            themes: ['identity'],
            representation: [],
            contentWarnings: ['violence'],
            confidenceNotes: 'confident',
            shortDescription: 'A spoiler-free blurb.',
            longSummary: 'A longer summary with the ending.',
            generatedAt: '2026-09-13T00:00:00+00:00',
          },
        },
        noBlurb: { title: 'No Blurb', llmTags: { genres: ['Fantasy'] } },
      },
    })
    expect(out.entries.full.llmTags).toEqual({
      ageRating: 'Teen',
      genres: ['Fantasy'],
      moods: ['tense'],
      themes: ['identity'],
      representation: [],
      contentWarnings: ['violence'],
      confidenceNotes: 'confident',
      shortDescription: 'A spoiler-free blurb.',
      longSummary: 'A longer summary with the ending.',
      generatedAt: '2026-09-13T00:00:00+00:00',
    })
    // No shortDescription — nothing sensible to showcase, so null.
    expect(out.entries.noBlurb.llmTags).toBeNull()
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

describe('normalise — collections', () => {
  it('keeps well-formed collections and coerces missing description to null', () => {
    const out = normalise({
      collections: {
        '1': { name: 'Fantasy Picks', description: 'Big fat fantasy', driveFileIds: ['d1', 'd2'] },
        '2': { name: 'No Description', driveFileIds: ['d3'] },
      },
    })
    expect(out.collections['1']).toEqual({
      name: 'Fantasy Picks',
      description: 'Big fat fantasy',
      driveFileIds: ['d1', 'd2'],
    })
    expect(out.collections['2']).toEqual({
      name: 'No Description',
      description: null,
      driveFileIds: ['d3'],
    })
  })

  it('drops a collection with no name, no driveFileIds array, or an empty member list', () => {
    const out = normalise({
      collections: {
        noName: { driveFileIds: ['d1'] } as unknown as { name: string; driveFileIds: string[] },
        noIds: { name: 'X' },
        empty: { name: 'Empty', driveFileIds: [] },
      },
    })
    expect(out.collections).toEqual({})
  })

  it('filters non-string entries out of driveFileIds', () => {
    const out = normalise({
      collections: { '1': { name: 'X', driveFileIds: ['d1', 5 as unknown as string] } },
    })
    expect(out.collections['1'].driveFileIds).toEqual(['d1'])
  })
})

describe('loadCachedIndex', () => {
  it('is EMPTY_INDEX with no cache', () => {
    expect(loadCachedIndex('lib-1')).toBe(EMPTY_INDEX)
  })

  it('returns the cached index only for a matching library folder', () => {
    const index = { entries: { a: { title: 'A' } }, series: {}, collections: {}, coversFolder: null }
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

  it('back-fills collections:{} into a pre-v8 cached index', () => {
    localStorage.setItem(
      'bookbrain.metadataIndex',
      JSON.stringify({
        libraryFolderId: 'lib-1',
        modifiedTime: 't',
        index: { entries: { a: { title: 'A' } }, series: {}, coversFolder: null },
      }),
    )
    expect(loadCachedIndex('lib-1').collections).toEqual({})
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
