import { useState } from 'react'
import { openLibraryCoverUrl } from '../lib/covers'
import { monthYear, type ReleaseItem } from '../lib/releases'
import type { RequestResult } from './BookRow'

interface Props {
  item: ReleaseItem
  onRequest: (item: ReleaseItem) => Promise<RequestResult>
  onClose: () => void
}

// The click-through for a cover in a release strip (and a row in
// <NewReleasesScreen>): what the book is, and a one-tap Request into the
// wishlist. Modelled on ReadersAlsoLiked's request button.
export function ReleaseCard({ item, onRequest, onClose }: Props) {
  const [state, setState] = useState<'idle' | 'pending' | RequestResult>('idle')
  const [coverBroken, setCoverBroken] = useState(false)
  const coverUrl =
    item.isbn13 && !coverBroken ? openLibraryCoverUrl(item.isbn13) : undefined

  async function request() {
    setState('pending')
    try {
      setState(await onRequest(item))
    } catch {
      setState('idle')
    }
  }

  const when = monthYear(item.releaseDate)
  const seriesLine = item.series
    ? `${item.series}${item.seriesPosition ? ` #${item.seriesPosition}` : ''}`
    : null

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-neutral-950/60 p-4 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="w-full max-w-sm rounded-xl bg-white p-4 shadow-xl dark:bg-neutral-900"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex gap-3">
          {coverUrl && (
            <img
              src={coverUrl}
              alt=""
              onError={() => setCoverBroken(true)}
              className="h-[120px] w-[80px] shrink-0 rounded object-cover ring-1 ring-black/5 dark:ring-white/10"
            />
          )}
          <div className="min-w-0 flex-1">
            <div className="font-medium text-neutral-900 dark:text-neutral-100">{item.title}</div>
            {item.author && (
              <div className="mt-0.5 text-sm text-neutral-500">{item.author}</div>
            )}
            {seriesLine && (
              <div className="mt-1">
                <span className="badge bg-brand-50 text-brand-700 dark:bg-brand-950/60 dark:text-brand-300">
                  {seriesLine}
                </span>
              </div>
            )}
            {when && <div className="mt-1 text-xs text-neutral-400">{when}</div>}
          </div>
        </div>

        {item.description && (
          <p className="mt-3 line-clamp-4 text-sm text-neutral-600 dark:text-neutral-400">
            {item.description}
          </p>
        )}

        {item.genres.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1">
            {item.genres.map((g) => (
              <span
                key={g}
                className="badge bg-neutral-100 text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300"
              >
                {g}
              </span>
            ))}
          </div>
        )}

        <div className="mt-4 flex items-center justify-between gap-2">
          {item.hardcoverSlug ? (
            <a
              href={`https://hardcover.app/series/${item.hardcoverSlug}`}
              target="_blank"
              rel="noreferrer"
              className="text-xs text-neutral-400 underline underline-offset-2 hover:text-neutral-600 dark:hover:text-neutral-300"
            >
              View on Hardcover
            </a>
          ) : (
            <span />
          )}
          <div className="flex gap-2">
            <button className="btn btn-neutral btn-xs" onClick={onClose}>
              Close
            </button>
            {state === 'owned' ? (
              <span className="badge bg-emerald-50 text-emerald-700 dark:bg-emerald-950/50 dark:text-emerald-400">
                In library
              </span>
            ) : state === 'added' || state === 'already-listed' ? (
              <span className="badge bg-neutral-100 text-neutral-500 dark:bg-neutral-800">
                On wishlist
              </span>
            ) : (
              <button
                className="btn btn-primary btn-xs"
                disabled={state === 'pending'}
                onClick={request}
              >
                {state === 'pending' ? '…' : 'Request'}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
