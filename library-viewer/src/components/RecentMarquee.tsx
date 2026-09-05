import { useCallback, useEffect, useState } from 'react'
import type { BookRow } from '../lib/books'
import { MarqueeCover } from './MarqueeCover'

// Roughly one cover every this-many seconds passes a fixed point — the loop
// duration scales with the cover count so the speed stays constant.
const SECONDS_PER_COVER = 3.2

interface Props {
  books: BookRow[]
  token: string
  onPick: (id: string) => void
}

function Track({
  books,
  token,
  large,
  ariaHidden,
  onPick,
  onResolved,
}: {
  books: BookRow[]
  token: string
  large: boolean
  ariaHidden?: boolean
  onPick: (id: string) => void
  onResolved: (driveId: string, hasImage: boolean) => void
}) {
  const size = large ? 'h-[210px] w-[140px]' : 'h-[72px] w-[48px]'
  return (
    <ul
      aria-hidden={ariaHidden}
      className={`flex shrink-0 items-center ${large ? 'gap-4 px-2' : 'gap-3 px-1.5'}`}
    >
      {books.map((b) => (
        <li key={b.id} className="shrink-0">
          <button
            type="button"
            tabIndex={ariaHidden ? -1 : undefined}
            title={`${b.title}${b.author ? ` — ${b.author}` : ''}`}
            aria-label={`Go to ${b.title}`}
            onClick={() => onPick(b.id)}
            className={`block ${size} overflow-hidden rounded shadow-sm ring-1 ring-black/5 transition-transform hover:scale-105 hover:shadow-md focus-visible:scale-105 dark:ring-white/10`}
          >
            <MarqueeCover token={token} driveId={b.id} isbn={b.isbn} onResolved={onResolved} />
          </button>
        </li>
      ))}
    </ul>
  )
}

export function RecentMarquee({ books, token, onPick }: Props) {
  const [results, setResults] = useState<Record<string, boolean>>({})
  const [tv, setTv] = useState(false)

  useEffect(() => {
    setResults({})
  }, [books])

  useEffect(() => {
    if (!tv) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setTv(false)
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [tv])

  const handleResolved = useCallback((driveId: string, hasImage: boolean) => {
    setResults((prev) => (prev[driveId] === hasImage ? prev : { ...prev, [driveId]: hasImage }))
  }, [])

  const settled = books.length > 0 && books.every((b) => b.id in results)
  // Drop books whose cover won't load — a blank tile isn't worth a slot.
  // Books still resolving stay in until they report back.
  const visible = books.filter((b) => results[b.id] !== false)
  // Duration tracks the full candidate count, not `visible` — so covers
  // dropping out as they fail to load doesn't lurch the scroll speed.
  const duration = `${Math.max(18, books.length * SECONDS_PER_COVER)}s`

  // Nothing worth showing, or the covers just aren't loading — stay out of
  // the way entirely.
  if (books.length < 2) return null
  if (settled && visible.length < 2) return null

  const strip = (large: boolean) => (
    <div className="marquee group relative">
      <div className="marquee-track flex w-max" style={{ animationDuration: duration }}>
        <Track books={visible} token={token} large={large} onPick={onPick} onResolved={handleResolved} />
        <Track
          books={visible}
          token={token}
          large={large}
          ariaHidden
          onPick={onPick}
          onResolved={handleResolved}
        />
      </div>
      {!large && (
        <>
          <div className="pointer-events-none absolute inset-y-0 left-0 w-8 bg-gradient-to-r from-neutral-50 to-transparent dark:from-neutral-950" />
          <div className="pointer-events-none absolute inset-y-0 right-0 w-8 bg-gradient-to-l from-neutral-50 to-transparent dark:from-neutral-950" />
        </>
      )}
    </div>
  )

  return (
    <>
      <div className="mb-3">
        <div className="mb-1 flex items-center justify-between px-1">
          <span className="text-[11px] font-semibold tracking-wide text-neutral-400 uppercase">
            Recently added
          </span>
          <button
            type="button"
            className="text-[11px] text-neutral-400 hover:text-brand-600 dark:hover:text-brand-400"
            onClick={() => setTv(true)}
          >
            Full screen ⤢
          </button>
        </div>
        {strip(false)}
      </div>

      {tv && (
        <div className="fixed inset-0 z-50 flex flex-col items-center justify-center gap-6 bg-neutral-950/95 backdrop-blur">
          <span className="text-sm font-semibold tracking-wide text-neutral-400 uppercase">
            Recently added
          </span>
          <div className="w-full">{strip(true)}</div>
          <button type="button" className="btn btn-neutral" onClick={() => setTv(false)}>
            Close (Esc)
          </button>
        </div>
      )}
    </>
  )
}
