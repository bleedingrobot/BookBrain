import { useEffect, useState } from 'react'
import type { BookRow } from '../lib/books'
import { useMarqueeCovers } from '../hooks/useMarqueeCovers'

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
  urls,
  large,
  ariaHidden,
  onPick,
}: {
  books: BookRow[]
  urls: Map<string, string>
  large: boolean
  ariaHidden?: boolean
  onPick: (id: string) => void
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
            className={`block ${size} overflow-hidden rounded bg-neutral-200/70 shadow-sm ring-1 ring-black/5 transition-transform hover:scale-105 hover:shadow-md focus-visible:scale-105 dark:bg-neutral-800 dark:ring-white/10`}
          >
            <img
              src={urls.get(b.id)}
              alt=""
              loading="eager"
              className="h-full w-full object-cover"
            />
          </button>
        </li>
      ))}
    </ul>
  )
}

function Skeleton({ large }: { large: boolean }) {
  const size = large ? 'h-[210px] w-[140px]' : 'h-[72px] w-[48px]'
  return (
    <div className={`flex items-center ${large ? 'gap-4 px-2' : 'gap-3 px-1.5'}`}>
      {Array.from({ length: large ? 8 : 14 }).map((_, i) => (
        <div
          key={i}
          className={`${size} shrink-0 animate-pulse rounded bg-neutral-200/70 dark:bg-neutral-800`}
        />
      ))}
    </div>
  )
}

export function RecentMarquee({ books, token, onPick }: Props) {
  const [tv, setTv] = useState(false)
  const { urls, done } = useMarqueeCovers(books, token)

  useEffect(() => {
    if (!tv) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setTv(false)
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [tv])

  // Only books with a resolved, known-good cover — never a blank tile.
  const shown = books.filter((b) => urls.has(b.id))
  const duration = `${Math.max(18, Math.max(shown.length, 8) * SECONDS_PER_COVER)}s`
  const loading = !done && shown.length < 2

  // Not enough recent books, or their covers just aren't available.
  if (books.length < 2) return null
  if (done && shown.length < 2) return null

  const strip = (large: boolean) =>
    loading ? (
      <Skeleton large={large} />
    ) : (
      <div className="marquee group relative">
        <div className="marquee-track flex w-max" style={{ animationDuration: duration }}>
          <Track books={shown} urls={urls} large={large} onPick={onPick} />
          <Track books={shown} urls={urls} large={large} ariaHidden onPick={onPick} />
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
          {!loading && (
            <button
              type="button"
              className="text-[11px] text-neutral-400 hover:text-brand-600 dark:hover:text-brand-400"
              onClick={() => setTv(true)}
            >
              Full screen ⤢
            </button>
          )}
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
