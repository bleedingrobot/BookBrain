import type { BookRow } from './books'

export interface SeriesGap {
  have: number[] // whole entry numbers present, sorted
  missing: number[] // whole numbers 1..runMax that aren't present
}

// The biggest jump between consecutive owned entries that still counts as
// "same numbered run". A companion short story with a junk sorting number
// (a real one seen in the wild: an EPUB tagged "Alexis Carew #301") sits
// hundreds above the actual books — without this, a tidy 6-book series
// reports ~300 missing volumes. Anything past a jump this large is treated
// as bonus/companion content: still listed in `have`, but it doesn't
// stretch the "missing" range.
const MAX_RUN_GAP = 12

// Which numbered entries of each series you have, and which are missing.
// Only whole numbers count — a #2.5 novella shouldn't make #2 or #3 look
// absent — and a "series" needs at least two entries in one run to have a
// gap worth mentioning.
export function computeSeriesGaps(rows: BookRow[]): Map<string, SeriesGap> {
  const byName = new Map<string, Set<number>>()
  for (const row of rows) {
    if (!row.series || row.seriesNumber == null) continue
    const n = Number(row.seriesNumber)
    if (!Number.isInteger(n) || n < 1) continue
    const set = byName.get(row.series) ?? new Set<number>()
    set.add(n)
    byName.set(row.series, set)
  }

  const out = new Map<string, SeriesGap>()
  for (const [name, set] of byName) {
    const have = [...set].sort((a, b) => a - b)

    // Walk up from the lowest owned entry, stopping at the first oversized
    // jump — everything up to there is the "real" numbered run.
    let runMax = have[0]
    let runCount = 1
    for (let k = 1; k < have.length; k++) {
      if (have[k] - runMax > MAX_RUN_GAP) break
      runMax = have[k]
      runCount++
    }
    if (runCount < 2) continue // not enough of a run to talk about gaps

    const missing: number[] = []
    for (let i = 1; i < runMax; i++) {
      if (!set.has(i)) missing.push(i)
    }
    out.set(name, { have, missing })
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
