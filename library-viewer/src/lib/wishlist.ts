// The wishlist lives as bookbrain-wishlist.json in the Drive library
// folder, so it syncs across devices. Read on demand, written on every
// change. Reconcile marks an item "acquired" once a matching book is in
// the library (by ISBN, then fuzzy title+author).
//
// Every item also records who asked for it (requestedBy — the self-picked
// viewer name, honesty-based, same as the activity log) and a status the
// household works through: wanted -> sourced -> acquired, or declined.
// "acquired" is set automatically by reconcile; the rest are set by hand
// on the Wishlist screen. There are no roles in this app — anyone who can
// open the library can add a request or move its status.

import type { BookRow } from './books'
import { readJsonFile, writeJsonFile } from './drive'
import type { BookHit } from './bookSearch'

const FILENAME = 'bookbrain-wishlist.json'

export type WishlistStatus = 'wanted' | 'sourced' | 'declined' | 'acquired'

const STATUSES: WishlistStatus[] = ['wanted', 'sourced', 'declined', 'acquired']

function isStatus(s: unknown): s is WishlistStatus {
  return typeof s === 'string' && (STATUSES as string[]).includes(s)
}

export interface WishlistItem {
  id: string
  title: string
  author: string | null
  series: string | null
  isbn13: string | null
  cover: string | null
  note: string
  // Who added the request (self-picked viewer name). Null for items added
  // before requests existed, or when no name is set.
  requestedBy: string | null
  // Progress. `acquired` (below) is kept in sync with `status === 'acquired'`
  // so an older viewer build that only knows the boolean still behaves.
  status: WishlistStatus
  // Free text for a non-wanted status — where it was ordered from, why it
  // was declined, etc.
  statusNote: string
  statusBy: string | null
  statusAt: string | null
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

// Inverse of what saveWishlist writes: pull a stored item list back into
// well-formed WishlistItems, filling defaults and migrating the old shape
// (pre-status items only had an `acquired` boolean).
export function normalizeItems(raw: Partial<WishlistItem>[] | undefined): WishlistItem[] {
  return (raw ?? [])
    .filter((i): i is Partial<WishlistItem> => typeof i?.title === 'string' && typeof i?.id === 'string')
    .map((i) => {
      const status: WishlistStatus = isStatus(i.status)
        ? i.status
        : i.acquired
          ? 'acquired'
          : 'wanted'
      return {
        id: i.id as string,
        title: i.title as string,
        author: i.author ?? null,
        series: i.series ?? null,
        isbn13: i.isbn13 ?? null,
        cover: i.cover ?? null,
        note: i.note ?? '',
        requestedBy: i.requestedBy ?? null,
        status,
        statusNote: i.statusNote ?? '',
        statusBy: i.statusBy ?? null,
        statusAt: i.statusAt ?? i.acquiredAt ?? null,
        addedAt: i.addedAt ?? '',
        acquired: status === 'acquired',
        acquiredAt: i.acquiredAt ?? null,
      }
    })
}

export async function loadWishlist(token: string, libraryFolderId: string): Promise<Wishlist> {
  try {
    const found = await readJsonFile<RawFile>(token, libraryFolderId, FILENAME)
    if (!found) return EMPTY_WISHLIST
    return {
      fileId: found.id,
      modifiedTime: found.modifiedTime,
      items: normalizeItems(found.content.items),
    }
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
    { version: 2, items: list.items },
    list.fileId,
  )
  return { ...list, fileId }
}

// Returns a new list with any not-yet-acquired item that's now in the
// library flipped to acquired, or the same list if nothing changed.
export function reconcile(list: Wishlist, rows: BookRow[]): { list: Wishlist; changed: boolean } {
  let changed = false
  const items = list.items.map((item) => {
    if (item.status === 'acquired') return item
    if (libraryMatch(item, rows) === null) return item
    changed = true
    const at = new Date().toISOString()
    return { ...item, status: 'acquired' as const, acquired: true, acquiredAt: at, statusAt: at }
  })
  return { list: changed ? { ...list, items } : list, changed }
}

// Apply a hand-set status change, stamping who/when.
export function withStatus(
  item: WishlistItem,
  status: WishlistStatus,
  by: string | null,
  statusNote?: string,
): WishlistItem {
  const at = new Date().toISOString()
  return {
    ...item,
    status,
    statusBy: by,
    statusAt: at,
    statusNote: statusNote ?? item.statusNote,
    acquired: status === 'acquired',
    acquiredAt: status === 'acquired' ? (item.acquiredAt ?? at) : null,
  }
}

export function hitToItem(hit: BookHit, requestedBy: string | null): WishlistItem {
  return {
    id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    title: hit.title,
    author: hit.author,
    series: hit.series,
    isbn13: hit.isbn13,
    cover: hit.cover,
    note: '',
    requestedBy,
    status: 'wanted',
    statusNote: '',
    statusBy: null,
    statusAt: null,
    addedAt: new Date().toISOString(),
    acquired: false,
    acquiredAt: null,
  }
}
