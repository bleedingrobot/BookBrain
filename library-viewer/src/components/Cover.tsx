import { useEffect, useRef, useState } from 'react'
import { fetchLocalCover, hasLocalCover, openLibraryCoverUrl } from '../lib/covers'

type State =
  | { kind: 'idle' }
  | { kind: 'local'; url: string }
  | { kind: 'openlib'; url: string }
  | { kind: 'none' }

// A ~34×50 book-cover thumbnail. Resolves lazily: nothing loads until the
// row scrolls near the viewport, then it tries the local Drive thumbnail,
// falls back to Open Library by ISBN, then to a placeholder.
export function Cover({
  token,
  driveId,
  isbn,
}: {
  token: string
  driveId: string
  isbn: string | null
}) {
  const [state, setState] = useState<State>({ kind: 'idle' })
  const ref = useRef<HTMLSpanElement>(null)

  useEffect(() => {
    setState({ kind: 'idle' })
    const el = ref.current
    if (!el) return
    let cancelled = false

    const resolve = async () => {
      if (hasLocalCover(driveId)) {
        const url = await fetchLocalCover(token, driveId)
        if (cancelled) return
        if (url) return setState({ kind: 'local', url })
      }
      if (cancelled) return
      if (isbn) return setState({ kind: 'openlib', url: openLibraryCoverUrl(isbn) })
      setState({ kind: 'none' })
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
      cancelled = true
      observer.disconnect()
    }
  }, [token, driveId, isbn])

  return (
    <span
      ref={ref}
      className="flex h-[50px] w-[34px] shrink-0 items-center justify-center overflow-hidden rounded bg-neutral-200/70 text-neutral-400 dark:bg-neutral-800"
    >
      {(state.kind === 'local' || state.kind === 'openlib') && (
        <img
          src={state.url}
          alt=""
          loading="lazy"
          className="h-full w-full object-cover"
          onError={() => setState({ kind: 'none' })}
        />
      )}
      {(state.kind === 'idle' || state.kind === 'none') && (
        <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M4 5a2 2 0 0 1 2-2h9l5 5v11a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5Z" />
        </svg>
      )}
    </span>
  )
}
