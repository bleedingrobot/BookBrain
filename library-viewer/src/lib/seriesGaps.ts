import type { BookRow } from './books'

export interface SeriesGap {
  have: number[] // whole entry numbers present, sorted
  missing: number[] // whole numbers 1..max(have) that aren't present
}

// Which numbered entries of each series you have, and which are missing.
// Only whole numbers count — a #2.5 novella shouldn't make #2 or #3 look
// absent — and a "series" needs at least two entries to have a gap.
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
    if (set.size < 2) continue
    const have = [...set].sort((a, b) => a - b)
    const missing: number[] = []
    for (let i = 1; i < have[have.length - 1]; i++) {
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
