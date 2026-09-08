import { useMemo, useState } from 'react'
import type { BookRow } from '../lib/books'
import { openLibraryCoverUrl } from '../lib/covers'
import { monthYear, type ReleaseItem } from '../lib/releases'
import { libraryMatch } from '../lib/wishlist'
import type { RequestResult } from './BookRow'

interface Props {
  recent: ReleaseItem[]
  upcoming: ReleaseItem[]
  allRows: BookRow[]
  onRequest: (item: ReleaseItem) => Promise<RequestResult>
  onBack: () => void
}

function Thumb({ isbn13 }: { isbn13: string | null }) {
  const [broken, setBroken] = useState(false)
  return (
    <span className="flex h-16 w-11 shrink-0 items-center justify-center overflow-hidden rounded bg-neutral-200 text-neutral-400 dark:bg-neutral-800">
      {isbn13 && !broken ? (
        <img
          src={openLibraryCoverUrl(isbn13)}
          alt=""
          className="h-full w-full object-cover"
          onError={() => setBroken(true)}
        />
      ) : (
        <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M4 5a2 2 0 0 1 2-2h9l5 5v11a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5Z" />
        </svg>
      )}
    </span>
  )
}

function ReleaseRow({
  item,
  owned,
  onRequest,
}: {
  item: ReleaseItem
  owned: boolean
  onRequest: (item: ReleaseItem) => Promise<RequestResult>
}) {
  const [state, setState] = useState<'idle' | 'pending' | RequestResult>('idle')
  const st = owned ? 'owned' : state
  const when = monthYear(item.releaseDate)

  async function request() {
    setState('pending')
    try {
      setState(await onRequest(item))
    } catch {
      setState('idle')
    }
  }

  return (
    <li className="flex items-start gap-3 py-3">
      <Thumb isbn13={item.isbn13} />
      <div className="min-w-0 flex-1">
        <div className="text-sm font-medium">{item.title}</div>
        <div className="truncate text-xs text-neutral-500">
          {item.series
            ? `${item.series}${item.seriesPosition ? ` #${item.seriesPosition}` : ''}`
            : (item.author ?? 'Unknown author')}
          {when ? ` · ${when}` : ''}
        </div>
        {item.genres.length > 0 && (
          <div className="mt-1 flex flex-wrap gap-1">
            {item.genres.slice(0, 3).map((g) => (
              <span
                key={g}
                className="badge bg-neutral-100 text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300"
              >
                {g}
              </span>
            ))}
          </div>
        )}
      </div>
      {st === 'owned' ? (
        <span className="badge bg-emerald-50 text-emerald-700 dark:bg-emerald-950/50 dark:text-emerald-400">
          In library
        </span>
      ) : st === 'added' || st === 'already-listed' ? (
        <span className="badge bg-neutral-100 text-neutral-500 dark:bg-neutral-800">On wishlist</span>
      ) : (
        <button className="btn btn-neutral btn-xs shrink-0" disabled={st === 'pending'} onClick={request}>
          {st === 'pending' ? '…' : 'Request'}
        </button>
      )}
    </li>
  )
}

export function NewReleasesScreen({ recent, upcoming, allRows, onRequest, onBack }: Props) {
  const [bucket, setBucket] = useState<'all' | 'recent' | 'upcoming'>('all')
  const [genre, setGenre] = useState<string | null>(null)

  const genres = useMemo(() => {
    const counts = new Map<string, number>()
    for (const item of [...recent, ...upcoming]) {
      for (const g of item.genres) counts.set(g, (counts.get(g) ?? 0) + 1)
    }
    return [...counts.entries()]
      .sort((a, b) => b[1] - a[1])
      .slice(0, 10)
      .map(([g]) => g)
  }, [recent, upcoming])

  const sections: { label: string; items: ReleaseItem[] }[] = []
  if (bucket !== 'upcoming' && recent.length) sections.push({ label: 'Recently released', items: recent })
  if (bucket !== 'recent' && upcoming.length) sections.push({ label: 'Coming soon', items: upcoming })

  return (
    <div className="mx-auto max-w-2xl px-4 py-5 sm:px-6">
      <button
        className="text-xs text-neutral-400 underline underline-offset-2 hover:text-neutral-600 dark:hover:text-neutral-300"
        onClick={onBack}
      >
        &larr; Back to library
      </button>

      <h1 className="mt-3 text-xl font-semibold tracking-tight">New &amp; upcoming</h1>
      <p className="mt-2 text-sm leading-relaxed text-neutral-500">
        Recent and announced books from the authors and series your library already knows — via
        Hardcover. Tap <em>Request</em> to add one to the wishlist.
      </p>

      <div className="mt-4 flex flex-wrap gap-1.5">
        {(['all', 'recent', 'upcoming'] as const).map((b) => (
          <button
            key={b}
            onClick={() => setBucket(b)}
            className={`rounded-full border px-2.5 py-1 text-xs font-medium transition-colors ${
              bucket === b
                ? 'border-brand-600 bg-brand-600 text-white'
                : 'border-neutral-300 bg-white text-neutral-600 hover:bg-neutral-100 dark:border-neutral-700 dark:bg-neutral-900 dark:text-neutral-300 dark:hover:bg-neutral-800'
            }`}
          >
            {b === 'all' ? 'All' : b === 'recent' ? 'Recent' : 'Upcoming'}
          </button>
        ))}
        {genres.map((g) => (
          <button
            key={g}
            onClick={() => setGenre((cur) => (cur === g ? null : g))}
            className={`rounded-full border px-2.5 py-1 text-xs font-medium transition-colors ${
              genre === g
                ? 'border-brand-600 bg-brand-600 text-white'
                : 'border-neutral-300 bg-white text-neutral-600 hover:bg-neutral-100 dark:border-neutral-700 dark:bg-neutral-900 dark:text-neutral-300 dark:hover:bg-neutral-800'
            }`}
          >
            {g}
          </button>
        ))}
      </div>

      {sections.length === 0 && (
        <p className="mt-6 text-sm text-neutral-400">Nothing to show yet — check back after the next sync.</p>
      )}

      {sections.map((section) => {
        const items = genre
          ? section.items.filter((i) => i.genres.includes(genre))
          : section.items
        if (items.length === 0) return null
        return (
          <div key={section.label} className="mt-6">
            <h2 className="text-sm font-medium text-neutral-500">{section.label}</h2>
            <ul className="mt-1 divide-y divide-neutral-100 dark:divide-neutral-800">
              {items.map((item) => (
                <ReleaseRow
                  key={item.key}
                  item={item}
                  owned={libraryMatch(item, allRows) != null}
                  onRequest={onRequest}
                />
              ))}
            </ul>
          </div>
        )
      })}
    </div>
  )
}
