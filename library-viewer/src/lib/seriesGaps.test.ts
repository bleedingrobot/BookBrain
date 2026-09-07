import { describe, expect, it } from 'vitest'
import type { BookRow } from './books'
import { computeSeriesGaps, incompleteSeriesNames } from './seriesGaps'

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
  }
}

describe('computeSeriesGaps', () => {
  it('reports the missing whole numbers up to the highest owned', () => {
    const gaps = computeSeriesGaps([
      book('Mistborn', '1'),
      book('Mistborn', '2'),
      book('Mistborn', '4'),
    ])
    expect(gaps.get('Mistborn')).toEqual({ have: [1, 2, 4], missing: [3] })
  })

  it('ignores fractional entries (novellas)', () => {
    const gaps = computeSeriesGaps([
      book('Stormlight', '1'),
      book('Stormlight', '2.5'),
      book('Stormlight', '3'),
    ])
    expect(gaps.get('Stormlight')).toEqual({ have: [1, 3], missing: [2] })
  })

  it('a single entry is not a series with gaps', () => {
    expect(computeSeriesGaps([book('One Book', '1')]).size).toBe(0)
  })

  it('a complete run has no missing entries', () => {
    const gaps = computeSeriesGaps([book('Dune', '1'), book('Dune', '2'), book('Dune', '3')])
    expect(gaps.get('Dune')).toEqual({ have: [1, 2, 3], missing: [] })
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
    expect(gaps.get('S')).toEqual({ have: [1, 10], missing: [2, 3, 4, 5, 6, 7, 8, 9] })
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
