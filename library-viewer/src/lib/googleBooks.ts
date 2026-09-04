// Browser-side Google Books search. No API key — the anonymous quota is
// small but wishlist lookups are rare, and a key would have to ship in the
// public bundle. If quota bites, a referrer-restricted key can be added to
// settings later.

export interface BookHit {
  title: string
  author: string | null
  series: string | null
  isbn13: string | null
  cover: string | null
  year: string | null
}

interface Volume {
  volumeInfo?: {
    title?: string
    authors?: string[]
    publishedDate?: string
    imageLinks?: { thumbnail?: string; smallThumbnail?: string }
    industryIdentifiers?: { type?: string; identifier?: string }[]
  }
}

function toHit(v: Volume): BookHit | null {
  const info = v.volumeInfo
  if (!info?.title) return null
  const isbn13 =
    info.industryIdentifiers?.find((i) => i.type === 'ISBN_13')?.identifier ?? null
  const thumb = info.imageLinks?.thumbnail ?? info.imageLinks?.smallThumbnail ?? null
  return {
    title: info.title,
    author: info.authors?.[0] ?? null,
    series: null,
    isbn13,
    cover: thumb ? thumb.replace(/^http:/, 'https:') : null,
    year: info.publishedDate?.slice(0, 4) ?? null,
  }
}

export async function searchGoogleBooks(query: string): Promise<BookHit[]> {
  const url =
    `https://www.googleapis.com/books/v1/volumes?maxResults=8&printType=books&q=` +
    encodeURIComponent(query.trim())
  const resp = await fetch(url)
  if (!resp.ok) {
    if (resp.status === 429) throw new Error('Google Books is rate-limited right now — try again shortly.')
    throw new Error(`Google Books error (${resp.status})`)
  }
  const data = (await resp.json()) as { items?: Volume[] }
  const seen = new Set<string>()
  const hits: BookHit[] = []
  for (const item of data.items ?? []) {
    const hit = toHit(item)
    if (!hit) continue
    const key = `${hit.title}|${hit.author}`.toLowerCase()
    if (seen.has(key)) continue
    seen.add(key)
    hits.push(hit)
  }
  return hits
}
