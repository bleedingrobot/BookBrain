import { describe, expect, it } from 'vitest'
import type { BookRow } from './books'
import type { BookHit } from './googleBooks'
import { alreadyListed, hitToItem, libraryMatch, reconcile, type WishlistItem } from './wishlist'

function row(partial: Partial<BookRow>): BookRow {
  return {
    id: partial.id ?? 'f1',
    file: { id: partial.id ?? 'f1', name: partial.filename ?? 'book.epub' },
    filename: partial.filename ?? 'book.epub',
    title: partial.title ?? 'Untitled',
    author: partial.author ?? null,
    series: partial.series ?? null,
    seriesNumber: partial.seriesNumber ?? null,
    description: null,
    addedAt: null,
    isbn: partial.isbn ?? null,
  }
}

function item(partial: Partial<WishlistItem>): WishlistItem {
  return {
    id: partial.id ?? 'w1',
    title: partial.title ?? 'Untitled',
    author: partial.author ?? null,
    series: null,
    isbn13: partial.isbn13 ?? null,
    cover: null,
    note: '',
    addedAt: '',
    acquired: partial.acquired ?? false,
    acquiredAt: partial.acquiredAt ?? null,
  }
}

function hit(partial: Partial<BookHit>): BookHit {
  return {
    title: partial.title ?? 'Untitled',
    author: partial.author ?? null,
    series: null,
    isbn13: partial.isbn13 ?? null,
    cover: null,
    year: null,
  }
}

describe('libraryMatch', () => {
  it('matches on ISBN first', () => {
    const rows = [row({ id: 'a', title: 'Totally Different', isbn: '9781234567897' })]
    expect(libraryMatch(item({ title: 'Whatever', isbn13: '9781234567897' }), rows)?.id).toBe('a')
  })

  it('matches on normalised title + author when no ISBN', () => {
    const rows = [row({ id: 'b', title: 'The Way of Kings', author: 'Brandon Sanderson' })]
    const m = libraryMatch(item({ title: 'Way of Kings', author: 'Sanderson, Brandon' }), rows)
    expect(m?.id).toBe('b')
  })

  it('ignores subtitles when comparing titles', () => {
    const rows = [row({ id: 'c', title: 'Dune' })]
    expect(libraryMatch(item({ title: 'Dune: Deluxe Edition' }), rows)?.id).toBe('c')
  })

  it('returns null when the author differs', () => {
    const rows = [row({ title: 'Foundation', author: 'Someone Else' })]
    expect(libraryMatch(item({ title: 'Foundation', author: 'Isaac Asimov' }), rows)).toBeNull()
  })

  it('returns null on no match', () => {
    expect(libraryMatch(item({ title: 'Nonexistent' }), [row({ title: 'Other' })])).toBeNull()
  })
})

describe('alreadyListed', () => {
  it('is true for a title+author already on the list', () => {
    const items = [item({ title: 'Mistborn', author: 'Brandon Sanderson' })]
    expect(alreadyListed(hit({ title: 'Mistborn', author: 'Brandon Sanderson' }), items)).toBe(true)
  })

  it('is false for a new book', () => {
    const items = [item({ title: 'Mistborn', author: 'Brandon Sanderson' })]
    expect(alreadyListed(hit({ title: 'Elantris', author: 'Brandon Sanderson' }), items)).toBe(false)
  })
})

describe('reconcile', () => {
  it('flips a wanted item to acquired once the library has it', () => {
    const list = { fileId: null, modifiedTime: null, items: [item({ id: 'x', title: 'Dune' })] }
    const { list: next, changed } = reconcile(list, [row({ title: 'Dune' })])
    expect(changed).toBe(true)
    expect(next.items[0].acquired).toBe(true)
    expect(next.items[0].acquiredAt).not.toBeNull()
  })

  it('leaves already-acquired items alone and reports no change', () => {
    const list = {
      fileId: null,
      modifiedTime: null,
      items: [item({ id: 'x', title: 'Dune', acquired: true, acquiredAt: '2026-01-01' })],
    }
    const { list: next, changed } = reconcile(list, [row({ title: 'Dune' })])
    expect(changed).toBe(false)
    expect(next.items[0].acquiredAt).toBe('2026-01-01')
  })

  it('reports no change when nothing matches', () => {
    const list = { fileId: null, modifiedTime: null, items: [item({ title: 'Dune' })] }
    expect(reconcile(list, [row({ title: 'Neuromancer' })]).changed).toBe(false)
  })
})

describe('hitToItem', () => {
  it('carries fields across and starts un-acquired', () => {
    const wi = hitToItem(hit({ title: 'Dune', author: 'Frank Herbert', isbn13: '9780441013593' }))
    expect(wi).toMatchObject({
      title: 'Dune',
      author: 'Frank Herbert',
      isbn13: '9780441013593',
      acquired: false,
      acquiredAt: null,
    })
    expect(wi.id).toBeTruthy()
    expect(wi.addedAt).toBeTruthy()
  })
})
