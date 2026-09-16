import { describe, expect, it } from 'vitest'
import { authorAffinity, goalPace, normaliseReading, readingProfile } from './reading'

describe('normaliseReading', () => {
  it('keeps valid entries, coerces bad status to null, drops junk keys', () => {
    const out = normaliseReading({
      version: 1,
      reader: 'James',
      unmatched: { read: 12, want: 3 }, // reading missing → 0
      wantUnowned: [
        { title: 'Wanted One', author: 'A', isbn13: '9990000000001' },
        { title: '   ', author: 'B', isbn13: null }, // blank title → dropped
        null as unknown as { title: string },
      ],
      books: {
        a: { status: 'read', rating: 4.5, readDate: '2026-01-02', readCount: 2 },
        b: { status: 'want' },
        c: { status: 'bogus' as unknown as 'read', rating: 'x' as unknown as number },
        d: null as unknown as Record<string, unknown>,
      },
    })
    expect(out.reader).toBe('James')
    expect(out.unmatched).toEqual({ read: 12, want: 3, reading: 0 })
    expect(out.books.a).toEqual({ status: 'read', rating: 4.5, readDate: '2026-01-02', readCount: 2 })
    expect(out.books.b).toEqual({ status: 'want' })
    expect(out.books.c).toEqual({ status: null }) // bad status → null, bad rating dropped
    expect(out.books.d).toBeUndefined()
    // Part I — a mid-read progress fraction is kept; 0 and 1 are dropped
    expect(normaliseReading({ books: { x: { status: 'reading', progress: 0.4 } } }).books.x).toEqual({
      status: 'reading',
      progress: 0.4,
    })
    expect(normaliseReading({ books: { x: { status: 'read', progress: 1 } } }).books.x).toEqual({
      status: 'read',
    })
    expect(out.wantUnowned).toEqual([
      { title: 'Wanted One', author: 'A', isbn13: '9990000000001' },
    ])
  })

  it('defaults an empty file', () => {
    const out = normaliseReading({})
    expect(out).toEqual({
      reader: '',
      unmatched: { read: 0, want: 0, reading: 0 },
      wantUnowned: [],
      goal: null,
      partial: false,
      books: {},
    })
  })

  it('parses the partial flag', () => {
    expect(normaliseReading({ partial: true }).partial).toBe(true)
    expect(normaliseReading({ partial: 'yes' } as never).partial).toBe(false)
  })
})

describe('readingProfile', () => {
  it('counts read books per author, ignoring non-read statuses', () => {
    const reading = normaliseReading({
      books: {
        a: { status: 'read' },
        b: { status: 'read' },
        c: { status: 'want' },
        d: { status: 'read' },
      },
    })
    const rows = new Map([
      ['a', { author: 'Sanderson' }],
      ['b', { author: 'Sanderson' }],
      ['c', { author: 'Sanderson' }],
      ['d', { author: 'Le Guin' }],
    ])
    const profile = readingProfile(reading, rows)
    expect(profile.get('Sanderson')).toBe(2)
    expect(profile.get('Le Guin')).toBe(1)
  })
})

describe('reading goal', () => {
  it('parses a valid goal and rejects a broken one', () => {
    expect(
      normaliseReading({ goal: { year: 2026, target: 50, progress: 48 } }).goal,
    ).toEqual({ year: 2026, target: 50, progress: 48 })
    expect(normaliseReading({ goal: { year: 2026, target: 0, progress: 3 } }).goal).toBeNull()
    expect(normaliseReading({ goal: { target: 50 } as never }).goal).toBeNull()
    expect(normaliseReading({}).goal).toBeNull()
  })

  it('goalPace is ahead/behind an even year-long pace', () => {
    const goal = { year: 2026, target: 50, progress: 30 }
    // half way through the year, pace wants 25 → 30 read = +5 ahead
    expect(goalPace(goal, new Date('2026-07-02T00:00:00Z'))).toBe(5)
    // 90% through, pace wants 45 → 30 read = 15 behind
    expect(goalPace(goal, new Date('2026-11-25T00:00:00Z'))).toBe(-15)
  })
})

describe('authorAffinity', () => {
  it('averages your ratings per author and treats an unrated DNF as ~2', () => {
    const reading = normaliseReading({
      books: {
        a: { status: 'read', rating: 5 },
        b: { status: 'read', rating: 4 },
        c: { status: 'dnf' }, // no rating → 2
        d: { status: 'read' }, // read, no rating → ignored
        e: { status: 'want' }, // no rating → ignored
      },
    })
    const rows = new Map([
      ['a', { author: 'Fave' }],
      ['b', { author: 'Fave' }],
      ['c', { author: 'Nope' }],
      ['d', { author: 'Unknown' }],
      ['e', { author: 'Later' }],
    ])
    const aff = authorAffinity(reading, rows)
    expect(aff.get('Fave')).toBe(4.5)
    expect(aff.get('Nope')).toBe(2)
    expect(aff.has('Unknown')).toBe(false)
    expect(aff.has('Later')).toBe(false)
  })
})
