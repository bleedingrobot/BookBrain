// bookbrain-reading.json — the library owner's Hardcover reading status
// (Read / Reading / Want to read / DNF), rating and read date, matched to
// library books by the backend (prompts/30). USER data, not catalogue: it's
// one person's list (the `reader` name), shown attributed, never as
// anonymous/aggregate data. Its own sidecar, fetched lazily.
//
// Same best-effort + modifiedTime-gated localStorage cache as
// recommendations.ts.

const FILENAME = 'bookbrain-reading.json'
const CACHE_KEY = 'bookbrain.reading'

export type ReadingStatus = 'read' | 'reading' | 'want' | 'dnf'

export interface ReadingEntry {
  status: ReadingStatus | null
  rating?: number
  readDate?: string
  readCount?: number
  // prompts/31 Part I — Hardcover's reader position (0..1), when it has one
  // and the book is mid-read.
  progress?: number
  // prompts/30 Phase 3 — set locally when the change is queued but hasn't
  // reached Hardcover yet.
  pending?: boolean
}

// A want-to-read book (from Hardcover) that isn't in the library — a wishlist
// candidate. Only want-to-read is listed this way; read/reading stay as counts.
export interface WantCandidate {
  title: string
  author: string | null
  isbn13: string | null
}

// prompts/31 Part C — the owner's Hardcover reading goal. `progress` is
// Hardcover's own count (all books, not just ones in this library), which is
// what the viewer should show.
export interface ReadingGoal {
  year: number
  target: number
  progress: number
}

export interface Reading {
  reader: string
  unmatched: { read: number; want: number; reading: number }
  wantUnowned: WantCandidate[]
  goal: ReadingGoal | null
  // keyed by Drive file id
  books: Record<string, ReadingEntry>
}

export const EMPTY_READING: Reading = {
  reader: '',
  unmatched: { read: 0, want: 0, reading: 0 },
  wantUnowned: [],
  goal: null,
  books: {},
}

const STATUSES: ReadingStatus[] = ['read', 'reading', 'want', 'dnf']

interface RawFile {
  version?: number
  reader?: string
  unmatched?: Partial<Reading['unmatched']>
  wantUnowned?: Partial<WantCandidate>[]
  goal?: Partial<ReadingGoal>
  books?: Record<string, Partial<ReadingEntry>>
}

function normaliseGoal(raw: Partial<ReadingGoal> | undefined): ReadingGoal | null {
  if (
    !raw ||
    typeof raw.year !== 'number' ||
    typeof raw.target !== 'number' ||
    typeof raw.progress !== 'number' ||
    raw.target <= 0
  ) {
    return null
  }
  return { year: raw.year, target: raw.target, progress: Math.max(0, raw.progress) }
}

// How many books ahead of (or behind, if negative) an even year-long pace the
// reader is right now. `now` is injectable for tests.
export function goalPace(goal: ReadingGoal, now = new Date()): number {
  const start = Date.UTC(goal.year - 1, 11, 31)
  const end = Date.UTC(goal.year, 11, 31)
  const frac = Math.min(1, Math.max(0, (now.getTime() - start) / (end - start)))
  return Math.round(goal.progress - goal.target * frac)
}

interface Cached {
  libraryFolderId: string
  modifiedTime: string | null
  reading: Reading
}

export function normaliseReading(raw: RawFile): Reading {
  const books: Record<string, ReadingEntry> = {}
  for (const [id, e] of Object.entries(raw.books ?? {})) {
    if (!e || typeof e !== 'object') continue
    const status =
      typeof e.status === 'string' && (STATUSES as string[]).includes(e.status)
        ? (e.status as ReadingStatus)
        : null
    books[id] = {
      status,
      ...(typeof e.rating === 'number' ? { rating: e.rating } : {}),
      ...(typeof e.readDate === 'string' ? { readDate: e.readDate } : {}),
      ...(typeof e.readCount === 'number' ? { readCount: e.readCount } : {}),
      ...(typeof e.progress === 'number' && e.progress > 0 && e.progress < 1
        ? { progress: e.progress }
        : {}),
    }
  }
  const u = raw.unmatched ?? {}
  const wantUnowned: WantCandidate[] = []
  for (const w of raw.wantUnowned ?? []) {
    if (w && typeof w.title === 'string' && w.title.trim()) {
      wantUnowned.push({
        title: w.title,
        author: typeof w.author === 'string' ? w.author : null,
        isbn13: typeof w.isbn13 === 'string' ? w.isbn13 : null,
      })
    }
  }
  return {
    reader: typeof raw.reader === 'string' ? raw.reader : '',
    unmatched: {
      read: typeof u.read === 'number' ? u.read : 0,
      want: typeof u.want === 'number' ? u.want : 0,
      reading: typeof u.reading === 'number' ? u.reading : 0,
    },
    wantUnowned,
    goal: normaliseGoal(raw.goal),
    books,
  }
}

// prompts/30 Phase 2 — how many books you've *read* per author, from the
// reading sidecar joined to the library rows. Ranks the release strips so a
// new book from an author you read a lot outranks one you've read once.
export function readingProfile(
  reading: Reading,
  rowsById: Map<string, { author: string | null }>,
): Map<string, number> {
  const counts = new Map<string, number>()
  for (const [id, entry] of Object.entries(reading.books)) {
    if (entry.status !== 'read') continue
    const author = rowsById.get(id)?.author
    if (author) counts.set(author, (counts.get(author) ?? 0) + 1)
  }
  return counts
}

// prompts/31 Part B — your mean rating per author, from the reading sidecar
// joined to the library rows. Used to re-rank "readers also liked": a rec by
// an author you rate highly floats up, one you rate low (or DNF'd) sinks.
// Only authors with at least one rated book appear.
export function authorAffinity(
  reading: Reading,
  rowsById: Map<string, { author: string | null }>,
): Map<string, number> {
  const sum = new Map<string, number>()
  const n = new Map<string, number>()
  for (const [id, entry] of Object.entries(reading.books)) {
    // a DNF with no rating still reads as "not for me"
    const score = entry.rating ?? (entry.status === 'dnf' ? 2 : null)
    if (score == null) continue
    const author = rowsById.get(id)?.author
    if (!author) continue
    sum.set(author, (sum.get(author) ?? 0) + score)
    n.set(author, (n.get(author) ?? 0) + 1)
  }
  const out = new Map<string, number>()
  for (const [author, total] of sum) out.set(author, total / (n.get(author) ?? 1))
  return out
}

function readCache(): Cached | null {
  try {
    const raw = localStorage.getItem(CACHE_KEY)
    return raw ? (JSON.parse(raw) as Cached) : null
  } catch {
    return null
  }
}

export function clearReadingCache(): void {
  try {
    localStorage.removeItem(CACHE_KEY)
  } catch {
    /* private mode */
  }
}

export async function fetchReading(token: string, libraryFolderId: string): Promise<Reading> {
  const cached = readCache()
  const cacheValid = cached?.libraryFolderId === libraryFolderId
  try {
    const query = encodeURIComponent(
      `'${libraryFolderId}' in parents and name = '${FILENAME}' and trashed = false`,
    )
    const listResp = await fetch(
      `https://www.googleapis.com/drive/v3/files?q=${query}&fields=files(id,modifiedTime)&pageSize=1`,
      { headers: { Authorization: `Bearer ${token}` } },
    )
    if (!listResp.ok) throw new Error(`list ${listResp.status}`)
    const { files } = (await listResp.json()) as { files: { id: string; modifiedTime: string }[] }
    if (files.length === 0) return cacheValid ? cached!.reading : EMPTY_READING

    const { id, modifiedTime } = files[0]
    if (cacheValid && cached!.modifiedTime === modifiedTime) return cached!.reading

    const fileResp = await fetch(`https://www.googleapis.com/drive/v3/files/${id}?alt=media`, {
      headers: { Authorization: `Bearer ${token}` },
    })
    if (!fileResp.ok) throw new Error(`download ${fileResp.status}`)
    const reading = normaliseReading((await fileResp.json()) as RawFile)
    try {
      localStorage.setItem(CACHE_KEY, JSON.stringify({ libraryFolderId, modifiedTime, reading }))
    } catch {
      /* over quota / private mode */
    }
    return reading
  } catch {
    return cacheValid ? cached!.reading : EMPTY_READING
  }
}
