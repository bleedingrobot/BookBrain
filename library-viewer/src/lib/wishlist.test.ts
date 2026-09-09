import { afterEach, describe, expect, it, vi } from 'vitest'
import type { BookRow } from './books'
import type { BookHit } from './bookSearch'
import {
  addToWishlist,
  alreadyListed,
  hitToItem,
  libraryMatch,
  normalizeItems,
  reconcile,
  withStatus,
  type WishlistItem,
} from './wishlist'

const driveMock = vi.hoisted(() => ({ readJsonFile: vi.fn(), writeJsonFile: vi.fn() }))
vi.mock('./drive', () => driveMock)

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
    meta: partial.meta ?? null,
    reading: partial.reading ?? null,
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
    requestedBy: partial.requestedBy ?? null,
    status: partial.status ?? 'wanted',
    statusNote: partial.statusNote ?? '',
    statusBy: partial.statusBy ?? null,
    statusAt: partial.statusAt ?? null,
    addedAt: partial.addedAt ?? '',
    acquired: partial.acquired ?? partial.status === 'acquired',
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

describe('normalizeItems', () => {
  it('migrates a legacy acquired item to status "acquired"', () => {
    const [i] = normalizeItems([{ id: 'x', title: 'Dune', acquired: true, acquiredAt: '2026-01-01' }])
    expect(i.status).toBe('acquired')
    expect(i.acquired).toBe(true)
  })

  it('defaults a legacy un-acquired item to status "wanted"', () => {
    const [i] = normalizeItems([{ id: 'x', title: 'Dune', acquired: false }])
    expect(i.status).toBe('wanted')
    expect(i.requestedBy).toBeNull()
  })

  it('keeps a valid stored status and requester', () => {
    const [i] = normalizeItems([
      { id: 'x', title: 'Dune', status: 'sourced', requestedBy: 'Tess', statusBy: 'James' },
    ])
    expect(i.status).toBe('sourced')
    expect(i.requestedBy).toBe('Tess')
    expect(i.statusBy).toBe('James')
  })

  it('falls back to "wanted" for an unrecognised status string', () => {
    const [i] = normalizeItems([{ id: 'x', title: 'Dune', status: 'bogus' as never }])
    expect(i.status).toBe('wanted')
  })

  it('drops entries without an id or title', () => {
    expect(normalizeItems([{ title: 'No id' }, { id: 'only-id' }])).toHaveLength(0)
  })
})

describe('reconcile', () => {
  it('flips a wanted item to acquired once the library has it', () => {
    const list = { fileId: null, modifiedTime: null, items: [item({ id: 'x', title: 'Dune' })] }
    const { list: next, changed } = reconcile(list, [row({ title: 'Dune' })])
    expect(changed).toBe(true)
    expect(next.items[0].status).toBe('acquired')
    expect(next.items[0].acquired).toBe(true)
    expect(next.items[0].acquiredAt).not.toBeNull()
  })

  it('flips a sourced item to acquired too', () => {
    const list = {
      fileId: null,
      modifiedTime: null,
      items: [item({ id: 'x', title: 'Dune', status: 'sourced' })],
    }
    const { list: next, changed } = reconcile(list, [row({ title: 'Dune' })])
    expect(changed).toBe(true)
    expect(next.items[0].status).toBe('acquired')
  })

  it('leaves already-acquired items alone and reports no change', () => {
    const list = {
      fileId: null,
      modifiedTime: null,
      items: [item({ id: 'x', title: 'Dune', status: 'acquired', acquiredAt: '2026-01-01' })],
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

describe('withStatus', () => {
  it('stamps who and when, and keeps the acquired flag in sync', () => {
    const next = withStatus(item({ status: 'wanted' }), 'sourced', 'James', 'ordered')
    expect(next.status).toBe('sourced')
    expect(next.statusBy).toBe('James')
    expect(next.statusNote).toBe('ordered')
    expect(next.statusAt).toBeTruthy()
    expect(next.acquired).toBe(false)
  })

  it('sets acquiredAt when moved to acquired', () => {
    const next = withStatus(item({ status: 'wanted' }), 'acquired', 'James')
    expect(next.acquired).toBe(true)
    expect(next.acquiredAt).toBeTruthy()
  })

  it('leaves the existing note when none is passed', () => {
    const next = withStatus(item({ status: 'sourced', statusNote: 'from Amazon' }), 'declined', 'Tess')
    expect(next.statusNote).toBe('from Amazon')
  })
})

describe('addToWishlist', () => {
  afterEach(() => vi.clearAllMocks())

  it('appends a new book and stamps the requester', async () => {
    driveMock.readJsonFile.mockResolvedValue({ id: 'w', modifiedTime: 't', content: { items: [] } })
    driveMock.writeJsonFile.mockResolvedValue('w')

    const result = await addToWishlist(
      'tok',
      'lib',
      hit({ title: 'Red Rising', author: 'Pierce Brown' }),
      'Tess',
    )

    expect(result).toBe('added')
    const written = driveMock.writeJsonFile.mock.calls[0][3]
    expect(written.items[0]).toMatchObject({ title: 'Red Rising', requestedBy: 'Tess', status: 'wanted' })
  })

  it('is a no-op when the book is already owned', async () => {
    const rows = [row({ title: 'Red Rising', author: 'Pierce Brown' })]
    const result = await addToWishlist('tok', 'lib', hit({ title: 'Red Rising', author: 'Pierce Brown' }), 'Tess', rows)
    expect(result).toBe('owned')
    expect(driveMock.readJsonFile).not.toHaveBeenCalled()
  })

  it('is a no-op when the book is already on the list', async () => {
    driveMock.readJsonFile.mockResolvedValue({
      id: 'w',
      modifiedTime: 't',
      content: { items: [{ id: '1', title: 'Red Rising', author: 'Pierce Brown' }] },
    })
    const result = await addToWishlist('tok', 'lib', hit({ title: 'Red Rising', author: 'Pierce Brown' }), 'Tess')
    expect(result).toBe('already-listed')
    expect(driveMock.writeJsonFile).not.toHaveBeenCalled()
  })
})

describe('hitToItem', () => {
  it('carries fields across, records the requester, and starts wanted', () => {
    const wi = hitToItem(
      hit({ title: 'Dune', author: 'Frank Herbert', isbn13: '9780441013593' }),
      'Tess',
    )
    expect(wi).toMatchObject({
      title: 'Dune',
      author: 'Frank Herbert',
      isbn13: '9780441013593',
      requestedBy: 'Tess',
      status: 'wanted',
      acquired: false,
      acquiredAt: null,
    })
    expect(wi.id).toBeTruthy()
    expect(wi.addedAt).toBeTruthy()
  })
})
