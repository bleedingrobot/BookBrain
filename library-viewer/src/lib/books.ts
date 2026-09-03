import type { DriveFile } from './drive'
import type { LibraryIndex } from './libraryIndex'
import { parseFilename } from './parseFilename'

// One row = one Drive file, with metadata taken from the sidecar index when
// it's there and recovered from the organized filename when it isn't.
export interface BookRow {
  id: string
  file: DriveFile
  filename: string
  title: string
  author: string | null
  series: string | null
  seriesNumber: string | null
  description: string | null
  addedAt: string | null
}

export type SortKey = 'title' | 'author' | 'series' | 'added'

export const SORT_LABELS: Record<SortKey, string> = {
  title: 'Title',
  author: 'Author',
  series: 'Series',
  added: 'Recently added',
}

export function buildRows(files: DriveFile[], index: LibraryIndex): BookRow[] {
  return files.map((file) => {
    const meta = index[file.id]
    const parsed = parseFilename(file.name)
    return {
      id: file.id,
      file,
      filename: file.name,
      title: meta?.title ?? parsed.title,
      author: meta?.author ?? parsed.author,
      series: meta?.series ?? parsed.series,
      seriesNumber:
        meta?.seriesNumber != null ? String(meta.seriesNumber) : parsed.seriesNumber,
      description: meta?.description ?? null,
      addedAt: meta?.addedAt ?? null,
    }
  })
}

export function matchesRow(row: BookRow, query: string): boolean {
  const q = query.trim().toLowerCase()
  if (!q) return true
  const haystack =
    `${row.author ?? ''} ${row.title} ${row.series ?? ''} ${row.filename}`.toLowerCase()
  return q.split(/\s+/).every((term) => haystack.includes(term))
}

// Sorts after the low one so unknowns sink to the bottom of a name sort but
// don't collide with a real "A" author.
const LAST = '￿'
const num = (s: string | null) => (s == null || s === '' ? Number.POSITIVE_INFINITY : Number(s))

function byNameThenSeries(a: BookRow, b: BookRow, key: 'author' | 'series'): number {
  const an = (a[key] ?? LAST).toLowerCase()
  const bn = (b[key] ?? LAST).toLowerCase()
  if (an !== bn) return an < bn ? -1 : 1
  const as = (a.series ?? LAST).toLowerCase()
  const bs = (b.series ?? LAST).toLowerCase()
  if (as !== bs) return as < bs ? -1 : 1
  const asn = num(a.seriesNumber)
  const bsn = num(b.seriesNumber)
  if (asn !== bsn) return asn - bsn
  return a.title.localeCompare(b.title)
}

export const SORTS: Record<SortKey, (a: BookRow, b: BookRow) => number> = {
  title: (a, b) => a.title.localeCompare(b.title),
  author: (a, b) => byNameThenSeries(a, b, 'author'),
  series: (a, b) => byNameThenSeries(a, b, 'series'),
  // Newest first; anything with no known added-date drops to the end.
  added: (a, b) => (b.addedAt ?? '').localeCompare(a.addedAt ?? '') || a.title.localeCompare(b.title),
}

// The heading shown above a run of rows when a name sort is active — null
// means "no heading here" (title/added sorts, or the value is unknown).
export function groupHeading(row: BookRow, sort: SortKey): string | null {
  if (sort === 'author') return row.author
  if (sort === 'series') return row.series
  return null
}
