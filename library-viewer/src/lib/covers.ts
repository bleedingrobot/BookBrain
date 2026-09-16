// Cover thumbnails, resolved per book in this order:
//   1. a local thumbnail the backend put in the Drive covers/ folder
//      (covers/<driveFileId>.jpg) — authenticated fetch, cached as a blob URL
//   2. Open Library's cover-by-ISBN endpoint (public, no auth)
//   3. Google Books — by ISBN if there is one, else a title+author search;
//      the only tier that can still find something for a book with no ISBN
//      at all, which tends to be exactly the older/obscure titles least
//      likely to have an embedded cover either
//   4. nothing — the row shows a placeholder
// Only rows actually on screen ever trigger a fetch (see components/Cover).

const blobUrlByBook = new Map<string, string>()
// In-flight fetches, so the several places a cover can be rendered at once
// (a list row, the recently-added ticker, its duplicated track) share one
// Drive request instead of racing — the loser used to leak its blob URL.
const inFlightByBook = new Map<string, Promise<string | null>>()
let coverFileIdByBook: Map<string, string> = new Map()
let manifestFolderId: string | null = null

export function openLibraryCoverUrl(isbn: string): string {
  // default=false → 404 (not a blank placeholder image) when unknown, so
  // the <img> onError can hide it.
  return `https://covers.openlibrary.org/b/isbn/${encodeURIComponent(isbn)}-M.jpg?default=false`
}

export function hasLocalCover(driveId: string): boolean {
  return coverFileIdByBook.has(driveId)
}

// Resolved once per (isbn, or title+author) and remembered — many rows can
// share the same lookup (a recommendation and the shelf copy of the same
// book), and a book without a cover shouldn't re-query Google every render.
const googleBooksCoverCache = new Map<string, string | null>()

export async function fetchGoogleBooksCoverUrl(
  isbn: string | null,
  title: string,
  author: string | null,
): Promise<string | null> {
  const key = isbn ?? `${title}|${author ?? ''}`
  const cached = googleBooksCoverCache.get(key)
  if (cached !== undefined) return cached

  const query = isbn
    ? `isbn:${isbn}`
    : `intitle:${title}${author ? `+inauthor:${author}` : ''}`
  try {
    const resp = await fetch(
      `https://www.googleapis.com/books/v1/volumes?q=${encodeURIComponent(query)}&maxResults=1`,
    )
    if (!resp.ok) throw new Error(`google books ${resp.status}`)
    const data = (await resp.json()) as {
      items?: { volumeInfo?: { imageLinks?: { thumbnail?: string; smallThumbnail?: string } } }[]
    }
    const links = data.items?.[0]?.volumeInfo?.imageLinks
    const raw = links?.thumbnail ?? links?.smallThumbnail ?? null
    // Google serves these over plain http:// by default, which the browser
    // blocks as mixed content on an https page.
    const url = raw ? raw.replace(/^http:\/\//, 'https://') : null
    googleBooksCoverCache.set(key, url)
    return url
  } catch {
    googleBooksCoverCache.set(key, null)
    return null
  }
}

// Lists the covers/ folder once per folder id and builds driveFileId → cover
// file id. Cheap (one paged listing); safe to call on every sync.
export async function loadCoverManifest(
  token: string,
  coversFolderId: string | null,
): Promise<void> {
  if (!coversFolderId) {
    coverFileIdByBook = new Map()
    manifestFolderId = null
    return
  }
  if (coversFolderId === manifestFolderId && coverFileIdByBook.size > 0) return
  try {
    const map = new Map<string, string>()
    let pageToken: string | undefined
    do {
      const q = encodeURIComponent(`'${coversFolderId}' in parents and trashed = false`)
      const page = pageToken ? `&pageToken=${pageToken}` : ''
      const resp = await fetch(
        `https://www.googleapis.com/drive/v3/files?q=${q}&fields=nextPageToken,files(id,name)&pageSize=1000${page}`,
        { headers: { Authorization: `Bearer ${token}` } },
      )
      if (!resp.ok) throw new Error(`covers list ${resp.status}`)
      const data = (await resp.json()) as {
        files: { id: string; name: string }[]
        nextPageToken?: string
      }
      for (const f of data.files) {
        if (f.name.endsWith('.jpg')) map.set(f.name.slice(0, -4), f.id)
      }
      pageToken = data.nextPageToken
    } while (pageToken)
    coverFileIdByBook = map
    manifestFolderId = coversFolderId
  } catch {
    // keep whatever we had
  }
}

export async function fetchLocalCover(token: string, driveId: string): Promise<string | null> {
  const cached = blobUrlByBook.get(driveId)
  if (cached) return cached
  const pending = inFlightByBook.get(driveId)
  if (pending) return pending
  const coverId = coverFileIdByBook.get(driveId)
  if (!coverId) return null

  const request = (async () => {
    try {
      const resp = await fetch(`https://www.googleapis.com/drive/v3/files/${coverId}?alt=media`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!resp.ok) return null
      const url = URL.createObjectURL(await resp.blob())
      blobUrlByBook.set(driveId, url)
      return url
    } catch {
      return null
    } finally {
      inFlightByBook.delete(driveId)
    }
  })()
  inFlightByBook.set(driveId, request)
  return request
}

export function clearCoverCache(): void {
  for (const url of blobUrlByBook.values()) URL.revokeObjectURL(url)
  blobUrlByBook.clear()
  coverFileIdByBook = new Map()
  manifestFolderId = null
}
