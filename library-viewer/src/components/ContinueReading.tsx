import { useEffect, useMemo, useState } from 'react'
import type { BookRow } from '../lib/books'
import { allProgress, continueReadingIds } from '../lib/readingProgress'
import { Cover } from './Cover'

interface Props {
  rows: BookRow[]
  token: string
  // Bumped by App whenever the reader closes, so the strip re-reads progress.
  tick: number
  onRead: (fileId: string) => void
}

export function ContinueReading({ rows, token, tick, onRead }: Props) {
  const [map, setMap] = useState(allProgress)
  useEffect(() => setMap(allProgress()), [tick])

  const items = useMemo(() => {
    const byId = new Map(rows.map((r) => [r.id, r]))
    return continueReadingIds(map, new Set(byId.keys()))
      .map((id) => ({ row: byId.get(id)!, pct: map[id].percent }))
      .filter((x) => x.row)
  }, [map, rows])

  if (items.length === 0) return null

  return (
    <section className="mb-4">
      <h2 className="mb-2 text-xs font-semibold tracking-wide text-neutral-500 uppercase dark:text-neutral-400">
        Continue reading
      </h2>
      <ul className="flex gap-2 overflow-x-auto pb-1">
        {items.map(({ row, pct }) => (
          <li key={row.id} className="shrink-0">
            <button
              type="button"
              className="flex w-52 items-center gap-2.5 rounded-lg border border-neutral-200 bg-white p-2 text-left hover:bg-neutral-50 dark:border-neutral-800 dark:bg-neutral-900 dark:hover:bg-neutral-800"
              onClick={() => onRead(row.id)}
            >
              <Cover token={token} driveId={row.id} isbn={row.isbn} />
              <div className="min-w-0 flex-1">
                <p className="truncate text-xs font-medium text-neutral-800 dark:text-neutral-200">
                  {row.title}
                </p>
                {row.author && (
                  <p className="truncate text-[11px] text-neutral-400">{row.author}</p>
                )}
                <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-neutral-200 dark:bg-neutral-800">
                  <div
                    className="h-full bg-brand-500"
                    style={{ width: `${Math.round(pct * 100)}%` }}
                  />
                </div>
                <p className="mt-0.5 text-[11px] text-neutral-400">{Math.round(pct * 100)}%</p>
              </div>
            </button>
          </li>
        ))}
      </ul>
    </section>
  )
}
