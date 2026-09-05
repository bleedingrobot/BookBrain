import { SORTS, type BookRow } from './books'

// How many recently-added books the ticker cycles through.
export const MARQUEE_MAX = 40

// The most-recently-added books, newest first, capped — the feed for the
// scrolling "recently added" strip. Rows with no known added-date sort last
// under SORTS.added and are dropped here so the strip only shows books we
// can genuinely call recent.
export function pickRecentBooks(rows: BookRow[], max = MARQUEE_MAX): BookRow[] {
  return [...rows]
    .filter((r) => r.addedAt)
    .sort(SORTS.added)
    .slice(0, max)
}
