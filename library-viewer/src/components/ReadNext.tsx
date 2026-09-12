import type { NextRead } from '../lib/seriesGaps'
import { Cover } from './Cover'

interface Props {
  items: NextRead[]
  token: string
  // Open the reader on an .epub, otherwise jump the list to the book.
  onRead: (fileId: string) => void
  onOpen: (fileId: string) => void
  // X a card away for ~a month (it comes back if still unread).
  onSnooze?: (fileId: string) => void
}

const isEpub = (name: string) => name.toLowerCase().endsWith('.epub')

// prompts/30 Phase 2 — "Read next": the book after the last one you finished
// in a series you're partway through and already own.
export function ReadNext({ items, token, onRead, onOpen, onSnooze }: Props) {
  if (items.length === 0) return null
  return (
    <section className="mb-4">
      <h2 className="mb-2 text-xs font-semibold tracking-wide text-neutral-500 uppercase dark:text-neutral-400">
        Read next
      </h2>
      <ul className="flex gap-2 overflow-x-auto pb-1">
        {items.map(({ row, seriesName, position, readThrough }) => (
          <li key={row.id} className="relative shrink-0">
            <button
              type="button"
              className="flex w-56 items-center gap-2.5 rounded-lg border border-neutral-200 bg-white p-2 text-left hover:bg-neutral-50 dark:border-neutral-800 dark:bg-neutral-900 dark:hover:bg-neutral-800"
              onClick={() => (isEpub(row.filename) ? onRead(row.id) : onOpen(row.id))}
            >
              <Cover token={token} driveId={row.id} isbn={row.isbn} />
              <div className="min-w-0 flex-1">
                <p className="truncate pr-4 text-xs font-medium text-neutral-800 dark:text-neutral-200">
                  {row.title}
                </p>
                <p className="truncate text-[11px] text-neutral-400">
                  {seriesName} #{position}
                </p>
                <p className="mt-1 text-[11px] text-emerald-600 dark:text-emerald-400">
                  ✓ read through #{readThrough}
                </p>
              </div>
            </button>
            {onSnooze && (
              <button
                type="button"
                aria-label="Hide for a month"
                title="Hide — comes back in a month if still unread"
                onClick={() => onSnooze(row.id)}
                className="absolute top-1 right-1 rounded p-1 text-sm leading-none text-neutral-300 hover:bg-neutral-100 hover:text-neutral-600 dark:hover:bg-neutral-800 dark:hover:text-neutral-300"
              >
                ✕
              </button>
            )}
          </li>
        ))}
      </ul>
    </section>
  )
}
