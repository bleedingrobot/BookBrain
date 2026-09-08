import { useEffect, useState } from 'react'

// The scrolling cover strip, shared by "Recently added" (RecentMarquee) and
// the "New / Coming soon in your series" release strips (ReleaseMarquee).
// Purely presentational: the caller resolves covers and hands in only the
// cards it has a known-good image for.

// Roughly one cover every this-many seconds passes a fixed point — the loop
// duration scales with the card count so the speed stays constant.
const SECONDS_PER_COVER = 3.2

export interface MarqueeCard {
  key: string
  url: string
  // Shown in the hover tooltip and as the button's accessible label.
  caption: string
}

interface Props {
  label: string
  cards: MarqueeCard[]
  loading: boolean
  // Below this many resolvable covers the strip hides itself entirely.
  minCards?: number
  onPick: (key: string) => void
}

function Track({
  cards,
  large,
  ariaHidden,
  onPick,
}: {
  cards: MarqueeCard[]
  large: boolean
  ariaHidden?: boolean
  onPick: (key: string) => void
}) {
  const size = large ? 'h-[210px] w-[140px]' : 'h-[144px] w-[96px]'
  return (
    <ul
      aria-hidden={ariaHidden}
      className={`flex shrink-0 items-center ${large ? 'gap-4 px-2' : 'gap-3 px-1.5'}`}
    >
      {cards.map((c) => (
        <li key={c.key} className="shrink-0">
          <button
            type="button"
            tabIndex={ariaHidden ? -1 : undefined}
            title={c.caption}
            aria-label={c.caption}
            onClick={() => onPick(c.key)}
            className={`block ${size} overflow-hidden rounded bg-neutral-200/70 shadow-sm ring-1 ring-black/5 transition-transform hover:scale-105 hover:shadow-md focus-visible:scale-105 dark:bg-neutral-800 dark:ring-white/10`}
          >
            <img src={c.url} alt="" loading="eager" className="h-full w-full object-cover" />
          </button>
        </li>
      ))}
    </ul>
  )
}

function Skeleton({ large }: { large: boolean }) {
  const size = large ? 'h-[210px] w-[140px]' : 'h-[144px] w-[96px]'
  return (
    <div className={`flex items-center ${large ? 'gap-4 px-2' : 'gap-3 px-1.5'}`}>
      {Array.from({ length: large ? 8 : 9 }).map((_, i) => (
        <div
          key={i}
          className={`${size} shrink-0 animate-pulse rounded bg-neutral-200/70 dark:bg-neutral-800`}
        />
      ))}
    </div>
  )
}

export function Marquee({ label, cards, loading, minCards = 2, onPick }: Props) {
  const [tv, setTv] = useState(false)

  useEffect(() => {
    if (!tv) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setTv(false)
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [tv])

  const duration = `${Math.max(18, Math.max(cards.length, 8) * SECONDS_PER_COVER)}s`
  const showSkeleton = loading && cards.length < 2

  // Nothing (yet) to show, or too few covers ever resolved.
  if (!loading && cards.length < minCards) return null

  const strip = (large: boolean) =>
    showSkeleton ? (
      <Skeleton large={large} />
    ) : (
      <div className="marquee group relative">
        <div className="marquee-track flex w-max" style={{ animationDuration: duration }}>
          <Track cards={cards} large={large} onPick={onPick} />
          <Track cards={cards} large={large} ariaHidden onPick={onPick} />
        </div>
        {!large && (
          <>
            <div className="pointer-events-none absolute inset-y-0 left-0 w-12 bg-gradient-to-r from-neutral-50 to-transparent dark:from-neutral-950" />
            <div className="pointer-events-none absolute inset-y-0 right-0 w-12 bg-gradient-to-l from-neutral-50 to-transparent dark:from-neutral-950" />
          </>
        )}
      </div>
    )

  return (
    <>
      <div className="mb-3">
        <div className="mb-1 flex items-center justify-between px-1">
          <span className="text-[11px] font-semibold tracking-wide text-neutral-400 uppercase">
            {label}
          </span>
          {!showSkeleton && (
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
            {label}
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
