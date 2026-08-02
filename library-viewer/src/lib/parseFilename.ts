export interface ParsedBook {
  author: string | null
  title: string
  series: string | null
  seriesNumber: string | null
}

// Mirrors the backend's organize_service naming convention:
// "Author, Title, Series, Part N.epub" or "Author, Title.epub", trimmed
// down to whichever fields are known. Falls back to the raw filename as
// the title for anything that doesn't match (manually-added files, etc.).
export function parseFilename(filename: string): ParsedBook {
  const withoutExt = filename.replace(/\.(epub|kpub)$/i, '')
  const parts = withoutExt.split(',').map((p) => p.trim())

  if (parts.length === 1) {
    return { author: null, title: parts[0], series: null, seriesNumber: null }
  }
  if (parts.length === 2) {
    return { author: parts[0], title: parts[1], series: null, seriesNumber: null }
  }
  if (parts.length === 3) {
    return { author: parts[0], title: parts[1], series: parts[2], seriesNumber: null }
  }
  return {
    author: parts[0],
    title: parts[1],
    series: parts[2],
    seriesNumber: parts.slice(3).join(', '),
  }
}

export function matchesSearch(book: ParsedBook, filename: string, query: string): boolean {
  if (!query.trim()) return true
  const haystack = `${book.author ?? ''} ${book.title} ${book.series ?? ''} ${filename}`.toLowerCase()
  return query
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean)
    .every((term) => haystack.includes(term))
}
