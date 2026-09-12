import { useMemo, useState } from 'react'
import type { BookRow } from '../lib/books'
import type { Prompts } from '../lib/prompts'
import { Cover } from './Cover'

export function PromptsScreen({
  prompts,
  rows,
  token,
  onBack,
  onOpen,
}: {
  prompts: Prompts
  rows: BookRow[]
  token: string
  onBack: () => void
  onOpen: (row: BookRow) => void
}) {
  const byId = useMemo(() => new Map(rows.map((r) => [r.id, r])), [rows])
  const [open, setOpen] = useState<number | null>(0)

  return (
    <div className="mx-auto max-w-2xl px-4 py-5 sm:px-6">
      <button
        className="text-xs text-neutral-400 underline underline-offset-2 hover:text-neutral-600 dark:hover:text-neutral-300"
        onClick={onBack}
      >
        &larr; Back to library
      </button>

      <h1 className="mt-3 text-xl font-semibold tracking-tight">Your library answers</h1>
      <p className="mt-2 text-sm leading-relaxed text-neutral-500">
        Popular Hardcover community questions, each showing the books in this library that answer
        it.
      </p>

      {prompts.prompts.length === 0 && (
        <p className="mt-6 text-sm text-neutral-400">
          Nothing yet — this fills in once the library&rsquo;s books are matched to Hardcover.
        </p>
      )}

      <ul className="mt-4 divide-y divide-neutral-100 dark:divide-neutral-800">
        {prompts.prompts.map((p, i) => {
          const books = p.driveIds.map((id) => byId.get(id)).filter((r): r is BookRow => !!r)
          if (books.length < 2) return null
          const isOpen = open === i
          return (
            <li key={p.question} className="py-3">
              <button
                className="flex w-full items-baseline justify-between gap-3 text-left"
                onClick={() => setOpen(isOpen ? null : i)}
              >
                <span className="text-sm font-medium">{p.question}</span>
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
