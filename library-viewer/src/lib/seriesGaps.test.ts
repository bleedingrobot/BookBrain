import { describe, expect, it } from 'vitest'
import type { BookRow } from './books'
import type { SeriesCatalog } from './libraryIndex'
import {
  collectSeriesReleases,
  comingSoonSeriesNames,
  computeSeriesGaps,
  incompleteSeriesNames,
  nextInSeries,
} from './seriesGaps'
import type { ReadingStatus } from './reading'

function catalog(
  slug: string,
  books: { position: number; title: string; releaseDate?: string | null; isbn13?: string | null }[],
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
    reading: null,
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

  it('builds a request-ready entry (with isbn13) for each named missing title', () => {
    const withIsbn = {
      Mistborn: catalog('mistborn', [
        { position: 1, title: 'The Final Empire' },
        { position: 2, title: 'The Well of Ascension', isbn13: '9780765316882' },
        { position: 3, title: 'The Hero of Ages' },
      ]),
    }
    const gap = computeSeriesGaps(rows, withIsbn).get('Mistborn')!
    expect(gap.missingEntries).toEqual([
      {
        seriesName: 'Mistborn',
        hardcoverSlug: 'mistborn',
        position: 2,
        title: 'The Well of Ascension',
        releaseDate: null,
        isbn13: '9780765316882',
      },
    ])
  })

  it('does not call a title-matching owned book "missing" even if it is tagged under a different series name', () => {
    // Real case: "The Worst Witch" #7 got organised with series tag "Worst
    // Witch" (no article) — invisible to this series' owned-set by name, but
    // the book is still on the shelf under that exact title.
    const misTagged = { ...book('Worst Witch', '2'), title: 'The Well of Ascension' }
    const gap = computeSeriesGaps([...rows, misTagged], cat).get('Mistborn')!
    expect(gap.missing).toEqual([])
    expect(gap.missingEntries).toEqual([])
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
    expect(gap.nextUp).toMatchObject({ position: 3, title: 'Oathbringer', releaseDate: iso(-30) })
    expect(gap.nextUp).toMatchObject({ seriesName: 'Stormlight', hardcoverSlug: 'stormlight' })
    expect(gap.upcoming).toMatchObject([{ position: 4, title: 'Rhythm of War', releaseDate: iso(90) }])
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
    expect(gap.upcoming).toMatchObject([
      { position: 2, title: 'Words of Radiance', releaseDate: iso(120) },
    ])
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

describe('collectSeriesReleases (Part 1)', () => {
  it('flattens nextUp + upcoming across every series, recent newest-first', () => {
    const gaps = computeSeriesGaps(
      [
        book('A', '1'),
        book('A', '2'),
        book('B', '1'),
        book('B', '2'),
      ],
      {
        A: catalog('a', [
          { position: 1, title: 'A1', releaseDate: '2010-01-01' },
          { position: 2, title: 'A2', releaseDate: '2012-01-01' },
          { position: 3, title: 'A3', releaseDate: iso(-10), isbn13: '9990000000001' }, // out
          { position: 4, title: 'A4', releaseDate: iso(200) }, // future
        ]),
        B: catalog('b', [
          { position: 1, title: 'B1', releaseDate: '2015-01-01' },
          { position: 2, title: 'B2', releaseDate: '2018-01-01' },
          { position: 3, title: 'B3', releaseDate: iso(50) }, // future
        ]),
      },
    )
    const { recent, upcoming } = collectSeriesReleases(gaps)
    expect(recent.map((e) => e.title)).toEqual(['A3'])
    expect(recent[0].isbn13).toBe('9990000000001')
    expect(upcoming.map((e) => e.title)).toEqual(['B3', 'A4']) // soonest first
  })

  it('reclassifies an "upcoming" entry whose announced date has since passed', () => {
    const gaps = computeSeriesGaps([book('S', '1'), book('S', '2')], {
      S: catalog('s', [
        { position: 1, title: 'S1', releaseDate: '2010-01-01' },
        { position: 2, title: 'S2', releaseDate: '2012-01-01' },
        { position: 3, title: 'S3', releaseDate: iso(-3) }, // announced future, now out
      ]),
    })
    // fromCatalog already routes a past date to nextUp, so it lands in recent
    const { recent, upcoming } = collectSeriesReleases(gaps)
    expect(recent.map((e) => e.title)).toEqual(['S3'])
    expect(upcoming).toEqual([])
  })

  it('dedupes a shared book across two overlapping series and caps each list', () => {
    const shared = { position: 3, title: 'Shared', releaseDate: iso(-5), isbn13: '9991112223334' }
    const gaps = computeSeriesGaps(
      [book('X', '1'), book('X', '2'), book('Y', '1'), book('Y', '2')],
      {
        X: catalog('x', [
          { position: 1, title: 'X1', releaseDate: '2010-01-01' },
          { position: 2, title: 'X2', releaseDate: '2011-01-01' },
          shared,
        ]),
        Y: catalog('y', [
          { position: 1, title: 'Y1', releaseDate: '2010-01-01' },
          { position: 2, title: 'Y2', releaseDate: '2011-01-01' },
          shared,
        ]),
      },
    )
    expect(collectSeriesReleases(gaps).recent.map((e) => e.title)).toEqual(['Shared'])
  })

  it('flattens missing (below-owned) entries too, sorted by series then position', () => {
    const gaps = computeSeriesGaps(
      [book('A', '1'), book('A', '4'), book('B', '1'), book('B', '3')],
      {
        A: catalog('a', [
          { position: 1, title: 'A1' },
          { position: 2, title: 'A2', isbn13: '9990000000002' },
          { position: 3, title: 'A3' },
          { position: 4, title: 'A4' },
        ]),
        B: catalog('b', [
          { position: 1, title: 'B1' },
          { position: 2, title: 'B2' },
          { position: 3, title: 'B3' },
        ]),
      },
    )
    const { missing } = collectSeriesReleases(gaps)
    expect(missing.map((e) => `${e.seriesName}#${e.position}`)).toEqual(['A#2', 'A#3', 'B#2'])
    expect(missing[0].isbn13).toBe('9990000000002')
  })

  it('ignores guess-path series (no Hardcover catalogue)', () => {
    const gaps = computeSeriesGaps([book('G', '1'), book('G', '3')])
    expect(collectSeriesReleases(gaps)).toEqual({ recent: [], upcoming: [], missing: [] })
  })
})

describe('nextInSeries (Phase 2)', () => {
  const b = (series: string, n: string, status?: ReadingStatus, readDate?: string) => ({
    ...book(series, n),
    reading: status ? { status, ...(readDate ? { readDate } : {}) } : null,
  })

  it('surfaces the owned unread book right after your last read one', () => {
    const out = nextInSeries([
      b('Mistborn', '1', 'read'),
      b('Mistborn', '2'), // owned, unread
      b('Mistborn', '3'),
    ])
    expect(out).toHaveLength(1)
    expect(out[0]).toMatchObject({ seriesName: 'Mistborn', position: 2, readThrough: 1 })
  })

  it('orders most-recently-active series first', () => {
    const out = nextInSeries([
      b('Old', '1', 'read', '2020-01-01'),
      b('Old', '2'),
      b('New', '1', 'read', '2026-06-01'),
      b('New', '2'),
    ])
    expect(out.map((x) => x.seriesName)).toEqual(['New', 'Old'])
  })

  it('skips a series you have not started', () => {
    expect(nextInSeries([b('X', '1'), b('X', '2')])).toEqual([])
  })

  it('skips when the next book is already read', () => {
    expect(nextInSeries([b('X', '1', 'read'), b('X', '2', 'read')])).toEqual([])
  })

  it('skips when you own no unread next book (caught up / gap)', () => {
    // read #1, own only #3 — the immediate next (#2) isn't owned
    expect(nextInSeries([b('X', '1', 'read'), b('X', '3')])).toEqual([])
  })

  it('needs #1 read (a mid-series run without #1 is too messy to nudge)', () => {
    expect(nextInSeries([b('X', '2', 'read'), b('X', '3')])).toEqual([])
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
