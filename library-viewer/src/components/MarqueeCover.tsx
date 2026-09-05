import { useEffect, useRef, useState } from 'react'
import { fetchLocalCover, hasLocalCover, openLibraryCoverUrl } from '../lib/covers'

type State = { kind: 'loading' } | { kind: 'img'; url: string } | { kind: 'none' }

// Like Cover, but for the recently-added ticker: every item is effectively
// on screen at once, so there's no IntersectionObserver — the cover resolves
// the moment it mounts. It reports back whether a real image was found so
// the strip can hide itself when almost nothing loads.
export function MarqueeCover({
  token,
  driveId,
  isbn,
  onResolved,
}: {
  token: string
  driveId: string
  isbn: string | null
  onResolved: (driveId: string, hasImage: boolean) => void
}) {
  const [state, setState] = useState<State>({ kind: 'loading' })

  // Kept in a ref so an inline callback from the parent doesn't re-run the
  // resolve effect on every render.
  const reportRef = useRef(onResolved)
  reportRef.current = onResolved

  useEffect(() => {
    let cancelled = false
    const settle = (next: State) => {
      if (cancelled) return
      setState(next)
      reportRef.current(driveId, next.kind === 'img')
    }

    const resolve = async () => {
      if (hasLocalCover(driveId)) {
        const url = await fetchLocalCover(token, driveId)
        if (url) return settle({ kind: 'img', url })
      }
      if (cancelled) return
      if (isbn) return settle({ kind: 'img', url: openLibraryCoverUrl(isbn) })
      settle({ kind: 'none' })
    }

    void resolve()
    return () => {
      cancelled = true
    }
  }, [token, driveId, isbn])

  return (
    <span className="flex h-full w-full items-center justify-center overflow-hidden rounded bg-neutral-200/70 text-neutral-400 dark:bg-neutral-800">
      {state.kind === 'img' && (
        <img
          src={state.url}
          alt=""
          loading="eager"
          className="h-full w-full object-cover"
          onError={() => {
            setState({ kind: 'none' })
            reportRef.current(driveId, false)
          }}
        />
      )}
      {state.kind !== 'img' && (
        <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M4 5a2 2 0 0 1 2-2h9l5 5v11a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5Z" />
        </svg>
      )}
    </span>
  )
}
