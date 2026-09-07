import { describe, expect, it } from 'vitest'
import {
  allProgress,
  clearProgress,
  continueReadingIds,
  getProgress,
  setProgress,
  type ProgressMap,
} from './readingProgress'

describe('readingProgress store', () => {
  it('round-trips a saved position', () => {
    setProgress('book-1', 'epubcfi(/6/4!/4/2)', 0.33)
    const p = getProgress('book-1')
    expect(p?.cfi).toBe('epubcfi(/6/4!/4/2)')
    expect(p?.percent).toBeCloseTo(0.33)
    expect(p?.updatedAt).toBeGreaterThan(0)
  })

  it('clamps percent to 0..1', () => {
    setProgress('book-2', 'x', 1.9)
    expect(getProgress('book-2')?.percent).toBe(1)
  })

  it('clearProgress removes just one book', () => {
    setProgress('a', 'x', 0.1)
    setProgress('b', 'y', 0.2)
    clearProgress('a')
    expect(getProgress('a')).toBeNull()
    expect(getProgress('b')).not.toBeNull()
  })

  it('ignores malformed stored JSON', () => {
    localStorage.setItem('bookbrain.readingProgress', '{not json')
    expect(allProgress()).toEqual({})
  })
})

describe('continueReadingIds', () => {
  const map: ProgressMap = {
    finished: { cfi: 'x', percent: 0.99, updatedAt: 100 },
    unstarted: { cfi: 'x', percent: 0, updatedAt: 100 },
    recent: { cfi: 'x', percent: 0.5, updatedAt: 300 },
    older: { cfi: 'x', percent: 0.2, updatedAt: 200 },
    gone: { cfi: 'x', percent: 0.4, updatedAt: 400 },
  }
  const known = new Set(['finished', 'unstarted', 'recent', 'older'])

  it('returns started-but-unfinished books, newest first', () => {
    expect(continueReadingIds(map, known)).toEqual(['recent', 'older'])
  })

  it('drops books no longer in the library', () => {
    expect(continueReadingIds(map, known)).not.toContain('gone')
  })

  it('respects the limit', () => {
    expect(continueReadingIds(map, known, 1)).toEqual(['recent'])
  })
})
