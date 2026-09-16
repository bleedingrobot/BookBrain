// Browser-side book search for the wishlist. Tries Google Books first, then
// falls back to Open Library.
//
// Google's keyless endpoint shares ONE quota bucket per public IP across
// every anonymous caller on that network, and Google throttles it hard and
// unpredictably — a 429 there is common and isn't really "you". Two things
// blunt that: a referrer-restricted Google Books API key
// (DEFAULT_GOOGLE_BOOKS_API_KEY in config.ts — its own private quota, used
// when set), and an Open Library fallback (no key, far more forgiving) as
// the safety net when Google is unavailable regardless.

import { DEFAULT_GOOGLE_BOOKS_API_KEY } from './config'

export interface BookHit {
  title: string
  author: string | null
  series: string | null
  isbn13: string | null
  cover: string | null
  year: string | null
}

// ---- Google Books -----------------------------------------------------------

interface GoogleVolume {
  volumeInfo?: {
    title?: string
    authors?: string[]
    publishedDate?: string
    imageLinks?: { thumbnail?: string; smallThumbnail?: string }
    industryIdentifiers?: { type?: string; identifier?: string }[]
  }
}

function googleToHit(v: GoogleVolume): BookHit | null {
  const info = v.volumeInfo
  if (!info?.title) return null
  const isbn13 = info.industryIdentifiers?.find((i) => i.type === 'ISBN_13')?.identifier ?? null
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

async function searchGoogleBooks(query: string): Promise<BookHit[]> {
  const key = DEFAULT_GOOGLE_BOOKS_API_KEY.trim()
  const url =
    `https://www.googleapis.com/books/v1/volumes?maxResults=8&printType=books` +
    (key ? `&key=${encodeURIComponent(key)}` : '') +
    `&q=${encodeURIComponent(query.trim())}`
  const resp = await fetch(url)
  if (!resp.ok) throw new Error(`google ${resp.status}`)
  const data = (await resp.json()) as { items?: GoogleVolume[] }
  return dedupe((data.items ?? []).map(googleToHit))
}

// ---- Open Library ----------------------------------------------------------

interface OpenLibraryDoc {
  title?: string
  author_name?: string[]
  first_publish_year?: number
  isbn?: string[]
  cover_i?: number
}

function openLibraryToHit(d: OpenLibraryDoc): BookHit | null {
  if (!d.title) return null
  const isbn13 = d.isbn?.find((i) => i.replace(/[^0-9Xx]/g, '').length === 13) ?? null
  return {
    title: d.title,
    author: d.author_name?.[0] ?? null,
    series: null,
    isbn13: isbn13 ? isbn13.replace(/[^0-9Xx]/g, '') : null,
    cover: d.cover_i ? `https://covers.openlibrary.org/b/id/${d.cover_i}-M.jpg` : null,
    year: d.first_publish_year ? String(d.first_publish_year) : null,
  }
}

async function searchOpenLibrary(query: string): Promise<BookHit[]> {
  const url =
    `https://openlibrary.org/search.json?limit=8&fields=title,author_name,first_publish_year,isbn,cover_i&q=` +
    encodeURIComponent(query.trim())
  const resp = await fetch(url)
  if (!resp.ok) throw new Error(`openlibrary ${resp.status}`)
  const data = (await resp.json()) as { docs?: OpenLibraryDoc[] }
  return dedupe((data.docs ?? []).map(openLibraryToHit))
}

// ---- orchestration --------------------------------------------------------

function dedupe(hits: (BookHit | null)[]): BookHit[] {
  const seen = new Set<string>()
  const out: BookHit[] = []
  for (const hit of hits) {
    if (!hit) continue
    const key = `${hit.title}|${hit.author}`.toLowerCase()
    if (seen.has(key)) continue
    seen.add(key)
    out.push(hit)
  }
  return out
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

export async function searchBooks(query: string): Promise<BookHit[]> {
  // Google first — richer results — with one quick retry for a transient
  // throttle before giving up on it.
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      return await searchGoogleBooks(query)
    } catch {
      if (attempt === 0) await sleep(700)
    }
  }
  // Google is unavailable (quota, network, CORS blip) — fall back.
  try {
    return await searchOpenLibrary(query)
  } catch {
    throw new Error('Book search is unavailable right now — try again in a minute.')
  }
}
