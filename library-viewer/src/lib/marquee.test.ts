import { describe, expect, it } from 'vitest'
import type { BookRow } from './books'
import { pickRecentBooks } from './marquee'

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

describe('pickRecentBooks', () => {
  it('orders newest first', () => {
    const rows = [
      row({ id: 'a', addedAt: '2026-01-01' }),
      row({ id: 'c', addedAt: '2026-03-01' }),
      row({ id: 'b', addedAt: '2026-02-01' }),
    ]
    expect(pickRecentBooks(rows).map((r) => r.id)).toEqual(['c', 'b', 'a'])
  })

  it('drops books with no added-date', () => {
    const rows = [row({ id: 'a', addedAt: '2026-01-01' }), row({ id: 'b', addedAt: null })]
    expect(pickRecentBooks(rows).map((r) => r.id)).toEqual(['a'])
  })

  it('caps to the requested maximum', () => {
    const rows = Array.from({ length: 60 }, (_, i) =>
      row({ id: String(i), addedAt: `2026-01-${String((i % 28) + 1).padStart(2, '0')}` }),
    )
    expect(pickRecentBooks(rows, 40)).toHaveLength(40)
  })

  it('does not mutate the input array', () => {
    const rows = [row({ id: 'a', addedAt: '2026-01-01' }), row({ id: 'b', addedAt: '2026-02-01' })]
    pickRecentBooks(rows)
    expect(rows.map((r) => r.id)).toEqual(['a', 'b'])
  })
})
