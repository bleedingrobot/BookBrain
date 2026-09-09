import { describe, expect, it } from 'vitest'
import { normaliseReading } from './reading'

describe('normaliseReading', () => {
  it('keeps valid entries, coerces bad status to null, drops junk keys', () => {
    const out = normaliseReading({
      version: 1,
      reader: 'James',
      unmatched: { read: 12, want: 3 }, // reading missing → 0
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
  })

  it('defaults an empty file', () => {
    const out = normaliseReading({})
    expect(out).toEqual({ reader: '', unmatched: { read: 0, want: 0, reading: 0 }, books: {} })
  })
})
