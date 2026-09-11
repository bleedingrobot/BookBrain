import type { BookRow } from './books'
import type { SeriesCatalog } from './libraryIndex'

// A catalogue entry above what you own — the next released book you're
// missing, or a not-yet-published one. prompts/26 Part A; prompts/27 Part 1
// adds the series name / slug / isbn13 so `collectSeriesReleases` can flatten
// these into a library-wide "new & upcoming" feed.
export interface SeriesReleaseEntry {
  seriesName: string
  hardcoverSlug: string | null
  position: number
  title: string
  releaseDate: string | null
  isbn13: string | null
}

export interface SeriesGap {
  have: number[] // whole entry numbers present, sorted
  missing: number[] // whole numbers below the highest owned that aren't present
  source: 'hardcover' | 'guess'
  // Present only when source === 'hardcover': missing number -> its title.
  missingTitles?: Record<number, string>
  // Present only when source === 'hardcover': the same missing entries as
  // `missing`/`missingTitles`, but request-ready (isbn13 included) — one per
  // missing number that has a matching catalog title. Feeds the "Request"
  // buttons and the Wishlist screen's "Missing from your series" card.
  missingEntries?: SeriesReleaseEntry[]
  // Present only when source === 'hardcover': the Hardcover series slug, for
  // a "via Hardcover" link.
  hardcoverSlug?: string | null
  // source === 'hardcover' only: the lowest already-released entry above what
  // you own that you don't have ("what to buy next").
  nextUp?: SeriesReleaseEntry | null
  // source === 'hardcover' only: entries above what you own with a real
  // future release date ("what's coming").
  upcoming?: SeriesReleaseEntry[]
}

// A far-future date on a placeholder row ("Untitled Stormlight Archive #10",
// 2035-01-01) is not a real announcement. Treat a date this many years out,
// or an "Untitled …" title, as "no real date".
const FAR_FUTURE_YEARS = 3
const PLACEHOLDER_TITLE = /^untitled\b/i

function realReleaseDate(book: { title: string; releaseDate?: string | null }): Date | null {
  if (!book.releaseDate || PLACEHOLDER_TITLE.test(book.title)) return null
  const d = new Date(book.releaseDate)
  if (Number.isNaN(d.getTime())) return null
  const cutoff = new Date()
  cutoff.setFullYear(cutoff.getFullYear() + FAR_FUTURE_YEARS)
  return d > cutoff ? null : d
}

// The biggest jump between consecutive owned entries that still counts as
// "same numbered run". A companion short story with a junk sorting number
// (a real one seen in the wild: an EPUB tagged "Alexis Carew #301") sits
// hundreds above the actual books — without this, a tidy 6-book series
// reports ~300 missing volumes. Anything past a jump this large is treated
// as bonus/companion content: still listed in `have`, but it doesn't
// stretch the "missing" range. Only used on the guess path — a Hardcover
// catalogue is authoritative and doesn't need the heuristic.
const MAX_RUN_GAP = 12

function ownedWholeNumbers(rows: BookRow[]): Map<string, Set<number>> {
  const byName = new Map<string, Set<number>>()
  for (const row of rows) {
    if (!row.series || row.seriesNumber == null) continue
    const n = Number(row.seriesNumber)
    if (!Number.isInteger(n) || n < 1) continue
    const set = byName.get(row.series) ?? new Set<number>()
    set.add(n)
    byName.set(row.series, set)
  }
  return byName
}

// Missing entries from a Hardcover canonical list: every whole-numbered
// entry at or below the highest owned position that isn't owned. Capping at
// the highest owned position keeps unreleased / not-yet-bought later books
// out of "missing" — those are "what's next", not "what's absent".
function fromCatalog(name: string, owned: Set<number>, catalog: SeriesCatalog): SeriesGap | null {
  const have = [...owned].sort((a, b) => a - b)
  const maxOwned = have[have.length - 1]

  const bookAt = new Map<number, SeriesCatalog['books'][number]>()
  for (const b of catalog.books) {
    if (Number.isInteger(b.position) && b.position >= 1 && b.position <= maxOwned) {
      bookAt.set(b.position, b)
    }
  }

  const missing: number[] = []
  const missingTitles: Record<number, string> = {}
  const missingEntries: SeriesReleaseEntry[] = []
  for (let i = 1; i < maxOwned; i++) {
    if (owned.has(i)) continue
    missing.push(i)
    const b = bookAt.get(i)
    if (b) {
      missingTitles[i] = b.title
      missingEntries.push({
        seriesName: name,
        hardcoverSlug: catalog.hardcoverSlug,
        position: i,
        title: b.title,
        releaseDate: null, // already out — this is what makes it "missing"
        isbn13: b.isbn13 ?? null,
      })
    }
  }

  // Everything above what you own: split into already-out ("next up", the
  // single lowest one) and not-yet-released ("upcoming", all of them).
  const now = Date.now()
  let nextUp: SeriesReleaseEntry | null = null
  const upcoming: SeriesReleaseEntry[] = []
  const aboveOwned = catalog.books
    .filter((b) => Number.isInteger(b.position) && b.position > maxOwned && !owned.has(b.position))
    .sort((a, b) => a.position - b.position)
  for (const b of aboveOwned) {
    const date = realReleaseDate(b)
    const entry: SeriesReleaseEntry = {
      seriesName: name,
      hardcoverSlug: catalog.hardcoverSlug,
      position: b.position,
      title: b.title,
      releaseDate: b.releaseDate ?? null,
      isbn13: b.isbn13 ?? null,
    }
    if (date && date.getTime() > now) {
      upcoming.push(entry)
    } else if (date && !nextUp) {
      nextUp = entry
    }
  }

  if (have.length < 2 && !nextUp && upcoming.length === 0) return null
  return {
    have,
    missing,
    source: 'hardcover',
    missingTitles,
    missingEntries,
    hardcoverSlug: catalog.hardcoverSlug,
    nextUp,
    upcoming,
  }
}

// The original heuristic: walk up from the lowest owned entry, stop at the
// first oversized jump, report the whole numbers absent from that run.
function fromGuess(owned: Set<number>): SeriesGap | null {
  const have = [...owned].sort((a, b) => a - b)
  let runMax = have[0]
  let runCount = 1
  for (let k = 1; k < have.length; k++) {
    if (have[k] - runMax > MAX_RUN_GAP) break
    runMax = have[k]
    runCount++
  }
  if (runCount < 2) return null

  const missing: number[] = []
  for (let i = 1; i < runMax; i++) {
    if (!owned.has(i)) missing.push(i)
  }
  return { have, missing, source: 'guess' }
}

// Which numbered entries of each series you have, and which are missing.
// Uses Hardcover's canonical list when the index carries one for that
// series, otherwise falls back to the gap heuristic. Only whole numbers
// count, and a "series" needs at least two owned entries to be worth
// mentioning.
export function computeSeriesGaps(
  rows: BookRow[],
  catalog: Record<string, SeriesCatalog> = {},
): Map<string, SeriesGap> {
  const out = new Map<string, SeriesGap>()
  for (const [name, owned] of ownedWholeNumbers(rows)) {
    const cat = catalog[name]
    const gap =
      (cat && cat.books.length > 0 ? fromCatalog(name, owned, cat) : null) ?? fromGuess(owned)
    if (gap) out.set(name, gap)
  }
  return out
}

// prompts/30 Phase 2 — "Read next": series where you've read a contiguous run
// from #1 and own the next entry unread. Needs BookRow.reading populated.
export interface NextRead {
  seriesName: string
  row: BookRow
  position: number
  readThrough: number
  // The read date of the furthest book you've read in this series — the
  // list is ordered most-recently-active first so what you were last
  // reading sits at the front.
  lastReadAt: string
}

export function nextInSeries(rows: BookRow[]): NextRead[] {
  const bySeries = new Map<string, Map<number, BookRow>>()
  for (const r of rows) {
    if (!r.series || r.seriesNumber == null) continue
    const n = Number(r.seriesNumber)
    if (!Number.isInteger(n) || n < 1) continue
    const m = bySeries.get(r.series) ?? new Map<number, BookRow>()
    m.set(n, r)
    bySeries.set(r.series, m)
  }
  const out: NextRead[] = []
  for (const [name, byNum] of bySeries) {
    const read = new Set(
      [...byNum].filter(([, r]) => r.reading?.status === 'read').map(([n]) => n),
    )
    if (read.size === 0) continue // haven't started this series
    let through = 0
    while (read.has(through + 1)) through++
    if (through === 0) continue // read some but not #1 — too messy to nudge
    const nextRow = byNum.get(through + 1)
    if (nextRow && nextRow.reading?.status !== 'read') {
      out.push({
        seriesName: name,
        row: nextRow,
        position: through + 1,
        readThrough: through,
        lastReadAt: byNum.get(through)?.reading?.readDate ?? '',
      })
    }
  }
  return out.sort(
    (a, b) => b.lastReadAt.localeCompare(a.lastReadAt) || a.seriesName.localeCompare(b.seriesName),
  )
}

export function incompleteSeriesNames(gaps: Map<string, SeriesGap>): Set<string> {
  const names = new Set<string>()
  for (const [name, gap] of gaps) {
    if (gap.missing.length > 0) names.add(name)
  }
  return names
}

// prompts/27 Part 1 — flatten every series' above-what-you-own entries into
// two library-wide lists for the "New in your series" / "Coming soon in your
// series" strips. "recent" = the released-but-unowned `nextUp`, plus any
// `upcoming` entry whose announced date has since passed (Hardcover data
// lags); "upcoming" = still-future entries, soonest first. Deduped (a book
// can sit in two overlapping series) and capped. `missing` (2026-09-11) adds
// the below-what-you-own gaps the same way, for the Wishlist screen's
// "Missing from your series" card + the inline per-row Request buttons.
export interface CollectedReleases {
  recent: SeriesReleaseEntry[]
  upcoming: SeriesReleaseEntry[]
  missing: SeriesReleaseEntry[]
}

const RELEASE_CAP = 30

function dedupeReleases(list: SeriesReleaseEntry[]): SeriesReleaseEntry[] {
  const seen = new Set<string>()
  const out: SeriesReleaseEntry[] = []
  for (const entry of list) {
    const key = entry.isbn13 || `${entry.seriesName}#${entry.position}|${entry.title.toLowerCase()}`
    if (seen.has(key)) continue
    seen.add(key)
    out.push(entry)
  }
  return out
}

export function collectSeriesReleases(gaps: Map<string, SeriesGap>): CollectedReleases {
  const now = Date.now()
  const recent: SeriesReleaseEntry[] = []
  const upcoming: SeriesReleaseEntry[] = []
  const missing: SeriesReleaseEntry[] = []
  for (const gap of gaps.values()) {
    if (gap.source !== 'hardcover') continue
    if (gap.nextUp) recent.push(gap.nextUp)
    for (const entry of gap.upcoming ?? []) {
      const d = entry.releaseDate ? new Date(entry.releaseDate) : null
      const hasPassed = d != null && !Number.isNaN(d.getTime()) && d.getTime() <= now
      ;(hasPassed ? recent : upcoming).push(entry)
    }
    missing.push(...(gap.missingEntries ?? []))
  }
  recent.sort((a, b) => (b.releaseDate ?? '').localeCompare(a.releaseDate ?? ''))
  upcoming.sort((a, b) => (a.releaseDate ?? '').localeCompare(b.releaseDate ?? ''))
  missing.sort((a, b) => a.seriesName.localeCompare(b.seriesName) || a.position - b.position)
  return {
    recent: dedupeReleases(recent).slice(0, RELEASE_CAP),
    upcoming: dedupeReleases(upcoming).slice(0, RELEASE_CAP),
    missing: dedupeReleases(missing).slice(0, RELEASE_CAP),
  }
}

// Series with a book announced for the future you don't own — the "Coming
// soon" filter chip. prompts/26 Part A.
export function comingSoonSeriesNames(gaps: Map<string, SeriesGap>): Set<string> {
  const names = new Set<string>()
  for (const [name, gap] of gaps) {
    if (gap.upcoming && gap.upcoming.length > 0) names.add(name)
  }
  return names
}
