import { describe, expect, it } from 'vitest'
import type { BookRow } from './books'
import type { SeriesCatalog } from './libraryIndex'
import {
  comingSoonSeriesNames,
  computeSeriesGaps,
  incompleteSeriesNames,
} from './seriesGaps'

function catalog(
  slug: string,
  books: { position: number; title: string; releaseDate?: string | null }[],
): SeriesCatalog {
  return { hardcoverId: 1, hardcoverName: slug, hardcoverSlug: slug, primaryCount: books.length, books }
}

const iso = (offsetDays: number) =>
  new Date(Date.now() + offsetDays * 86_400_000).toISOString().slice(0, 10)

function book(series: string | null, seriesNumber: string | null): BookRow {
  return {
    id: `${series}-${seriesNumber}`,
    file: { id: 'x', name: 'x' },
    filename: 'x',
    title: 't',
    author: null,
    series,
    seriesNumber,
    description: null,
    addedAt: null,
    isbn: null,
    meta: null,
  }
}

describe('computeSeriesGaps (guess path)', () => {
  it('reports the missing whole numbers up to the highest owned', () => {
    const gaps = computeSeriesGaps([
      book('Mistborn', '1'),
      book('Mistborn', '2'),
      book('Mistborn', '4'),
    ])
    expect(gaps.get('Mistborn')).toMatchObject({ have: [1, 2, 4], missing: [3], source: 'guess' })
  })

  it('ignores fractional entries (novellas)', () => {
    const gaps = computeSeriesGaps([
      book('Stormlight', '1'),
      book('Stormlight', '2.5'),
      book('Stormlight', '3'),
    ])
    expect(gaps.get('Stormlight')).toMatchObject({ have: [1, 3], missing: [2] })
  })

  it('a single entry is not a series with gaps', () => {
    expect(computeSeriesGaps([book('One Book', '1')]).size).toBe(0)
  })

  it('a complete run has no missing entries', () => {
    const gaps = computeSeriesGaps([book('Dune', '1'), book('Dune', '2'), book('Dune', '3')])
    expect(gaps.get('Dune')).toMatchObject({ have: [1, 2, 3], missing: [] })
  })

  it('skips books with no series or no number', () => {
    expect(computeSeriesGaps([book(null, '1'), book('S', null)]).size).toBe(0)
  })

  it('a wildly-out-of-band number (junk sorting placeholder) does not explode the missing list', () => {
    const gaps = computeSeriesGaps([
      book('Alexis Carew', '2'),
      book('Alexis Carew', '3'),
      book('Alexis Carew', '4'),
      book('Alexis Carew', '5'),
      book('Alexis Carew', '6'),
      book('Alexis Carew', '7'),
      book('Alexis Carew', '301'), // companion short story tagged "#301"
    ])
    const gap = gaps.get('Alexis Carew')!
    expect(gap.have).toContain(301)
    expect(gap.missing).toEqual([1]) // just the genuinely-absent #1, not #8..#300
  })

  it('two entries far enough apart are not treated as one run', () => {
    const gaps = computeSeriesGaps([book('S', '1'), book('S', '14')])
    // 14 - 1 > MAX_RUN_GAP, so the run is just [1] → not enough to flag
    expect(gaps.get('S')).toBeUndefined()
  })

  it('gaps within the jump limit are still reported', () => {
    const gaps = computeSeriesGaps([book('S', '1'), book('S', '10')])
    expect(gaps.get('S')).toMatchObject({ have: [1, 10], missing: [2, 3, 4, 5, 6, 7, 8, 9] })
  })
})

describe('computeSeriesGaps (Hardcover catalog path)', () => {
  const rows = [book('Mistborn', '1'), book('Mistborn', '3')]
  const cat = {
    Mistborn: catalog('mistborn', [
      { position: 0.5, title: 'The Eleventh Metal' },
      { position: 1, title: 'The Final Empire' },
      { position: 2, title: 'The Well of Ascension' },
      { position: 3, title: 'The Hero of Ages' },
      { position: 4, title: 'The Alloy of Law' },
    ]),
  }

  it('names the missing entry and marks the source', () => {
    const gap = computeSeriesGaps(rows, cat).get('Mistborn')!
    expect(gap.source).toBe('hardcover')
    expect(gap.missing).toEqual([2])
    expect(gap.missingTitles).toEqual({ 2: 'The Well of Ascension' })
    expect(gap.hardcoverSlug).toBe('mistborn')
  })

  it('never lists entries above the highest owned (unreleased / not-yet-bought)', () => {
    // owns 1 and 3 → #4 is in the catalog but must not show as "missing"
    const gap = computeSeriesGaps(rows, cat).get('Mistborn')!
    expect(gap.missing).not.toContain(4)
  })

  it('a complete owned run against the catalog reports nothing missing', () => {
    const gap = computeSeriesGaps(
      [book('Mistborn', '1'), book('Mistborn', '2'), book('Mistborn', '3')],
      cat,
    ).get('Mistborn')!
    expect(gap).toMatchObject({ source: 'hardcover', missing: [] })
  })

  it('falls back to the guess path when no catalog entry exists', () => {
    const gap = computeSeriesGaps(rows, {}).get('Mistborn')!
    expect(gap.source).toBe('guess')
    expect(gap.missingTitles).toBeUndefined()
  })
})

describe('computeSeriesGaps — next up / upcoming (Part A)', () => {
  const owned = [book('Stormlight', '1'), book('Stormlight', '2')]

  it('splits already-released vs future entries above what you own', () => {
    const cat = {
      Stormlight: catalog('stormlight', [
        { position: 1, title: 'The Way of Kings', releaseDate: '2010-08-31' },
        { position: 2, title: 'Words of Radiance', releaseDate: '2014-03-04' },
        { position: 3, title: 'Oathbringer', releaseDate: iso(-30) }, // out
        { position: 4, title: 'Rhythm of War', releaseDate: iso(90) }, // coming
      ]),
    }
    const gap = computeSeriesGaps(owned, cat).get('Stormlight')!
    expect(gap.nextUp).toEqual({ position: 3, title: 'Oathbringer', releaseDate: iso(-30) })
    expect(gap.upcoming).toEqual([{ position: 4, title: 'Rhythm of War', releaseDate: iso(90) }])
  })

  it('ignores far-future placeholder rows', () => {
    const cat = {
      Stormlight: catalog('stormlight', [
        { position: 1, title: 'The Way of Kings' },
        { position: 2, title: 'Words of Radiance' },
        { position: 6, title: 'Untitled Stormlight Archive #6', releaseDate: '2031-12-01' },
        { position: 7, title: 'The Real Title', releaseDate: '2044-01-01' }, // >3y out
      ]),
    }
    const gap = computeSeriesGaps(owned, cat).get('Stormlight')!
    expect(gap.nextUp).toBeNull()
    expect(gap.upcoming).toEqual([])
  })

  it('a single owned book still surfaces an upcoming entry', () => {
    const cat = {
      Stormlight: catalog('stormlight', [
        { position: 1, title: 'The Way of Kings', releaseDate: '2010-08-31' },
        { position: 2, title: 'Words of Radiance', releaseDate: iso(120) },
      ]),
    }
    const gap = computeSeriesGaps([book('Stormlight', '1')], cat).get('Stormlight')!
    expect(gap.upcoming).toEqual([{ position: 2, title: 'Words of Radiance', releaseDate: iso(120) }])
  })

  it('comingSoonSeriesNames is only series with a future entry', () => {
    const cat = {
      Stormlight: catalog('stormlight', [
        { position: 1, title: 'a' },
        { position: 2, title: 'b' },
        { position: 3, title: 'c', releaseDate: iso(60) },
      ]),
    }
    const gaps = computeSeriesGaps(owned, cat)
    expect([...comingSoonSeriesNames(gaps)]).toEqual(['Stormlight'])
  })
})

describe('incompleteSeriesNames', () => {
  it('is only the series that are actually missing something', () => {
    const gaps = computeSeriesGaps([
      book('Gappy', '1'),
      book('Gappy', '3'),
      book('Whole', '1'),
      book('Whole', '2'),
    ])
    expect([...incompleteSeriesNames(gaps)]).toEqual(['Gappy'])
  })
})
