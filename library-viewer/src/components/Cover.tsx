import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchGoogleBooksCoverUrl, fetchLocalCover, hasLocalCover, openLibraryCoverUrl } from '../lib/covers'

type State =
  | { kind: 'idle' }
  | { kind: 'local'; url: string }
  | { kind: 'openlib'; url: string }
  | { kind: 'googlebooks'; url: string }
  | { kind: 'none' }

// A ~34×50 book-cover thumbnail (or ~150×220 with `size="lg"`, for a single
// featured book rather than a dense list). Resolves lazily: nothing loads
// until the row scrolls near the viewport, then it tries the local Drive
// thumbnail, falls back to Open Library by ISBN, then Google Books (by ISBN
// if Open Library's image 404s, or by title+author when there's no ISBN at
// all), then a placeholder. `title` is only needed for that last, ISBN-less
// tier — omit it and the chain just skips straight to the placeholder once
// Open Library (or the lack of an ISBN) comes up empty.
export function Cover({
  token,
  driveId,
  isbn,
  title,
  author,
  size = 'sm',
}: {
  token: string
  driveId: string
  isbn: string | null
  title?: string
  author?: string | null
  size?: 'sm' | 'lg'
}) {
  const [state, setState] = useState<State>({ kind: 'idle' })
  const ref = useRef<HTMLSpanElement>(null)
  const cancelledRef = useRef(false)

  const tryGoogleBooks = useCallback(async () => {
    if (!isbn && !title) {
      setState({ kind: 'none' })
      return
    }
    const url = await fetchGoogleBooksCoverUrl(isbn, title ?? '', author ?? null)
    if (cancelledRef.current) return
    setState(url ? { kind: 'googlebooks', url } : { kind: 'none' })
  }, [isbn, title, author])

  useEffect(() => {
    setState({ kind: 'idle' })
    cancelledRef.current = false
    const el = ref.current
    if (!el) return

    const resolve = async () => {
      if (hasLocalCover(driveId)) {
        const url = await fetchLocalCover(token, driveId)
        if (cancelledRef.current) return
        if (url) return setState({ kind: 'local', url })
      }
      if (cancelledRef.current) return
      if (isbn) return setState({ kind: 'openlib', url: openLibraryCoverUrl(isbn) })
      await tryGoogleBooks()
    }

    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          observer.disconnect()
          void resolve()
        }
      },
      { rootMargin: '400px' },
    )
    observer.observe(el)
    return () => {
      cancelledRef.current = true
      observer.disconnect()
    }
  }, [token, driveId, isbn, tryGoogleBooks])

  const large = size === 'lg'

  return (
    <span
      ref={ref}
      className={
        large
          ? 'flex h-[220px] w-[150px] shrink-0 items-center justify-center overflow-hidden rounded-lg bg-neutral-200/70 text-neutral-400 shadow-lg ring-1 ring-black/5 dark:bg-neutral-800 dark:ring-white/10'
          : 'flex h-[50px] w-[34px] shrink-0 items-center justify-center overflow-hidden rounded bg-neutral-200/70 text-neutral-400 dark:bg-neutral-800'
      }
    >
      {(state.kind === 'local' || state.kind === 'openlib' || state.kind === 'googlebooks') && (
        <img
          src={state.url}
          alt=""
          loading="lazy"
          className="h-full w-full object-cover"
          onError={() => {
            // Open Library's image 404s as a plain broken image, not a
            // rejected fetch — this is the only way to learn it came up
            // empty and fall through to Google Books.
            if (state.kind === 'openlib') void tryGoogleBooks()
            else setState({ kind: 'none' })
          }}
        />
      )}
      {(state.kind === 'idle' || state.kind === 'none') && (
        <svg
          viewBox="0 0 24 24"
          className={large ? 'h-12 w-12' : 'h-4 w-4'}
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
        >
          <path d="M4 5a2 2 0 0 1 2-2h9l5 5v11a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5Z" />
        </svg>
      )}
    </span>
  )
}
