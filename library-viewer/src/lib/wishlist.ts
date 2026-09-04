// The wishlist lives as bookbrain-wishlist.json in the Drive library
// folder, so it syncs across devices. Read on demand, written on every
// change. Reconcile marks an item "acquired" once a matching book is in
// the library (by ISBN, then fuzzy title+author).

import type { BookRow } from './books'
import { readJsonFile, writeJsonFile } from './drive'
import type { BookHit } from './googleBooks'

const FILENAME = 'bookbrain-wishlist.json'

export interface WishlistItem {
  id: string
  title: string
  author: string | null
  series: string | null
  isbn13: string | null
  cover: string | null
  note: string
  addedAt: string
  acquired: boolean
  acquiredAt: string | null
}

export interface Wishlist {
  fileId: string | null
  modifiedTime: string | null
  items: WishlistItem[]
}

export const EMPTY_WISHLIST: Wishlist = { fileId: null, modifiedTime: null, items: [] }

interface RawFile {
  version?: number
  items?: Partial<WishlistItem>[]
}

function normTitle(s: string | null): string {
  if (!s) return ''
  return s
    .toLowerCase()
    .replace(/\s*[:;(].*$/, '') // drop subtitle / parenthetical
    .replace(/^(the|a|an)\s+/, '')
    .replace(/[^a-z0-9]+/g, '')
}

function normWords(s: string | null): string {
  if (!s) return ''
  return (s.toLowerCase().match(/[a-z0-9]+/g) ?? []).sort().join(' ')
}

// A library row that is the same book as this item, or null.
export function libraryMatch(
  item: Pick<WishlistItem, 'title' | 'author' | 'isbn13'>,
  rows: BookRow[],
): BookRow | null {
  if (item.isbn13) {
    const byIsbn = rows.find((r) => r.isbn && r.isbn === item.isbn13)
    if (byIsbn) return byIsbn
  }
  const t = normTitle(item.title)
  const a = item.author ? normWords(item.author) : null
  return (
    rows.find(
      (r) => normTitle(r.title) === t && (a === null || (r.author != null && normWords(r.author) === a)),
    ) ?? null
  )
}

export function alreadyListed(hit: BookHit, items: WishlistItem[]): boolean {
  const t = normTitle(hit.title)
  const a = hit.author ? normWords(hit.author) : null
  return items.some(
    (i) => normTitle(i.title) === t && (a === null || (i.author && normWords(i.author) === a)),
  )
}

export async function loadWishlist(token: string, libraryFolderId: string): Promise<Wishlist> {
  try {
    const found = await readJsonFile<RawFile>(token, libraryFolderId, FILENAME)
    if (!found) return EMPTY_WISHLIST
    const items = (found.content.items ?? [])
      .filter((i): i is WishlistItem => typeof i?.title === 'string' && typeof i?.id === 'string')
      .map((i) => ({
        id: i.id,
        title: i.title,
        author: i.author ?? null,
        series: i.series ?? null,
        isbn13: i.isbn13 ?? null,
        cover: i.cover ?? null,
        note: i.note ?? '',
        addedAt: i.addedAt ?? '',
        acquired: Boolean(i.acquired),
        acquiredAt: i.acquiredAt ?? null,
      }))
    return { fileId: found.id, modifiedTime: found.modifiedTime, items }
  } catch {
    return EMPTY_WISHLIST
  }
}

export async function saveWishlist(
  token: string,
  libraryFolderId: string,
  list: Wishlist,
): Promise<Wishlist> {
  const fileId = await writeJsonFile(
    token,
    libraryFolderId,
    FILENAME,
    { version: 1, items: list.items },
    list.fileId,
  )
  return { ...list, fileId }
}

// Returns a new list with any wanted item that's now in the library flipped
// to acquired, or the same list if nothing changed.
export function reconcile(list: Wishlist, rows: BookRow[]): { list: Wishlist; changed: boolean } {
  let changed = false
  const items = list.items.map((item) => {
    if (item.acquired) return item
    if (libraryMatch(item, rows) === null) return item
    changed = true
    return { ...item, acquired: true, acquiredAt: new Date().toISOString() }
  })
  return { list: changed ? { ...list, items } : list, changed }
}

export function hitToItem(hit: BookHit): WishlistItem {
  return {
    id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    title: hit.title,
    author: hit.author,
    series: hit.series,
    isbn13: hit.isbn13,
    cover: hit.cover,
    note: '',
    addedAt: new Date().toISOString(),
    acquired: false,
    acquiredAt: null,
  }
}
