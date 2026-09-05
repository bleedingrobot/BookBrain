import { useEffect, useState } from 'react'
import type { BookRow } from '../lib/books'
import { fetchLocalCover, hasLocalCover, openLibraryCoverUrl } from '../lib/covers'

interface Result {
  // driveId → a resolved, known-good cover URL (local blob or Open Library).
  urls: Map<string, string>
  // true once every book has been tried — lets the strip give up if almost
  // nothing resolved.
  done: boolean
}

// Resolves covers for the recently-added ticker up front, the same chain as
// components/Cover (local Drive thumbnail → Open Library by ISBN) but eager
// and *verified*: an Open Library URL is only handed back after a fetch
// confirms the image exists, so the strip never has to render a tile it
// can't fill. Failures are simply omitted.
export function useMarqueeCovers(books: BookRow[], token: string): Result {
  const [urls, setUrls] = useState<Map<string, string>>(new Map())
  const [done, setDone] = useState(false)

  useEffect(() => {
    let cancelled = false
    const resolved = new Map<string, string>()
    setUrls(new Map())
    setDone(false)

    const commit = () => {
      if (!cancelled) setUrls(new Map(resolved))
    }

    const one = async (book: BookRow) => {
      if (hasLocalCover(book.id)) {
        const local = await fetchLocalCover(token, book.id)
        if (cancelled) return
        if (local) {
          resolved.set(book.id, local)
          commit()
          return
        }
      }
      if (cancelled || !book.isbn) return
      const url = openLibraryCoverUrl(book.isbn)
      // Preload it: onload means Open Library really has this cover (the
      // default=false param makes a missing one 404 rather than a blank
      // placeholder), and it warms the cache so the <img> paints at once.
      const ok = await new Promise<boolean>((res) => {
        const probe = new Image()
        probe.onload = () => res(probe.naturalWidth > 1)
        probe.onerror = () => res(false)
        probe.src = url
      })
      if (!cancelled && ok) {
        resolved.set(book.id, url)
        commit()
      }
    }

    Promise.allSettled(books.map(one)).then(() => {
      if (!cancelled) setDone(true)
    })

    return () => {
      cancelled = true
    }
  }, [books, token])

  return { urls, done }
}
