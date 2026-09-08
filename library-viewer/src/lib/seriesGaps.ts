import type { BookRow } from './books'
import type { SeriesCatalog } from './libraryIndex'

export interface SeriesGap {
  have: number[] // whole entry numbers present, sorted
  missing: number[] // whole numbers below the highest owned that aren't present
  source: 'hardcover' | 'guess'
  // Present only when source === 'hardcover': missing number -> its title.
  missingTitles?: Record<number, string>
  // Present only when source === 'hardcover': the Hardcover series slug, for
  // a "via Hardcover" link.
  hardcoverSlug?: string | null
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
function fromCatalog(owned: Set<number>, catalog: SeriesCatalog): SeriesGap | null {
  const have = [...owned].sort((a, b) => a - b)
  if (have.length < 2) return null
  const maxOwned = have[have.length - 1]

  const titleAt = new Map<number, string>()
  for (const b of catalog.books) {
    if (Number.isInteger(b.position) && b.position >= 1 && b.position <= maxOwned) {
      titleAt.set(b.position, b.title)
    }
  }

  const missing: number[] = []
  const missingTitles: Record<number, string> = {}
  for (let i = 1; i < maxOwned; i++) {
    if (owned.has(i)) continue
    missing.push(i)
    const t = titleAt.get(i)
    if (t) missingTitles[i] = t
  }
  return { have, missing, source: 'hardcover', missingTitles, hardcoverSlug: catalog.hardcoverSlug }
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
    const gap = (cat && cat.books.length > 0 ? fromCatalog(owned, cat) : null) ?? fromGuess(owned)
    if (gap) out.set(name, gap)
  }
  return out
}

export function incompleteSeriesNames(gaps: Map<string, SeriesGap>): Set<string> {
  const names = new Set<string>()
  for (const [name, gap] of gaps) {
    if (gap.missing.length > 0) names.add(name)
  }
  return names
}
