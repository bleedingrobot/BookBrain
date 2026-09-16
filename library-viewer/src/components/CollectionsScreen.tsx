import { useMemo, useState } from 'react'
import type { BookRow } from '../lib/books'
import type { CollectionEntry } from '../lib/libraryIndex'
import { Cover } from './Cover'

export function CollectionsScreen({
  collections,
  rows,
  token,
  onBack,
  onOpen,
}: {
  collections: Record<string, CollectionEntry>
  rows: BookRow[]
  token: string
  onBack: () => void
  onOpen: (row: BookRow) => void
}) {
  const byId = useMemo(() => new Map(rows.map((r) => [r.id, r])), [rows])
  const entries = useMemo(() => Object.entries(collections), [collections])
  const [open, setOpen] = useState<string | null>(entries[0]?.[0] ?? null)

  return (
    <div className="mx-auto max-w-2xl px-4 py-5 sm:px-6">
      <button
        className="text-xs text-neutral-400 underline underline-offset-2 hover:text-neutral-600 dark:hover:text-neutral-300"
        onClick={onBack}
      >
        &larr; Back to library
      </button>

      <h1 className="mt-3 text-xl font-semibold tracking-tight">Collections</h1>
      <p className="mt-2 text-sm leading-relaxed text-neutral-500">
        Curated shelves — membership updates on its own as the library grows.
      </p>

      {entries.length === 0 && (
        <p className="mt-6 text-sm text-neutral-400">No collections yet.</p>
      )}

      <ul className="mt-4 divide-y divide-neutral-100 dark:divide-neutral-800">
        {entries.map(([id, collection]) => {
          const books = collection.driveFileIds
            .map((driveId) => byId.get(driveId))
            .filter((r): r is BookRow => !!r)
          if (books.length === 0) return null
          const isOpen = open === id
          return (
            <li key={id} className="py-3">
              <button
                className="flex w-full items-baseline justify-between gap-3 text-left"
                onClick={() => setOpen(isOpen ? null : id)}
              >
                <span>
                  <span className="text-sm font-medium">{collection.name}</span>
                  {collection.description && (
                    <span className="ml-2 text-xs text-neutral-400">{collection.description}</span>
                  )}
                </span>
                <span className="shrink-0 text-xs text-neutral-400">{books.length}</span>
              </button>
              {isOpen && (
                <ul className="mt-2 flex gap-2 overflow-x-auto pb-1">
                  {books.map((row) => (
                    <li key={row.id} className="w-16 shrink-0">
                      <button className="block w-full" onClick={() => onOpen(row)} title={row.title}>
                        <Cover token={token} driveId={row.id} isbn={row.isbn} />
                        <span className="mt-1 line-clamp-2 text-[11px] leading-tight text-neutral-500">
                          {row.title}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </li>
          )
        })}
      </ul>
    </div>
  )
}
