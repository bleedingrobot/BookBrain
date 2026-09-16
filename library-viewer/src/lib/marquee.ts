import { SORTS, type BookRow } from './books'

// How many recently-added books to feed the ticker. A few more than we'd
// ever show at once — items whose cover won't load get dropped, so start
// with some slack.
export const MARQUEE_MAX = 50

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
