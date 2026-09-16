import { useEffect, useMemo, useState } from 'react'
import type { BookRow } from '../lib/books'
import { timeAgo } from '../lib/news'
import { Cover } from './Cover'

function chipList(items: string[], tone: string) {
  return items.map((item) => (
    <span key={item} className={`badge ${tone}`}>
      {item}
    </span>
  ))
}

// prompts/38 — a rotating feature for one book whose local-LLM full-text
// pass is actually done, at the bottom of the main shelf. Picked at random
// from whatever's eligible; "Next" reshuffles to another. The long summary
// (which may include the ending) stays behind an explicit reveal — the
// short description is the only thing shown by default, same "no accidental
// spoilers" posture as the content-warnings disclosure elsewhere.
export function ShowcaseSection({
  rows,
  token,
  onOpenBook,
}: {
  rows: BookRow[]
  token: string
  onOpenBook: (id: string) => void
}) {
  const eligible = useMemo(
    () => rows.filter((r): r is BookRow & { llmTags: NonNullable<BookRow['llmTags']> } => !!r.llmTags),
    [rows],
  )
  const [currentId, setCurrentId] = useState<string | null>(null)

  useEffect(() => {
    if (eligible.length === 0) {
      setCurrentId(null)
      return
    }
    if (!currentId || !eligible.some((r) => r.id === currentId)) {
      setCurrentId(eligible[Math.floor(Math.random() * eligible.length)].id)
    }
  }, [eligible, currentId])

  if (eligible.length === 0) return null
  const book = eligible.find((r) => r.id === currentId) ?? eligible[0]
  const tags = book.llmTags

  function next() {
    if (eligible.length <= 1) return
    let pick = eligible[Math.floor(Math.random() * eligible.length)]
    while (pick.id === book.id) {
      pick = eligible[Math.floor(Math.random() * eligible.length)]
    }
    setCurrentId(pick.id)
  }

  return (
    <section className="mt-8">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-xs font-semibold tracking-wide text-neutral-500 uppercase dark:text-neutral-400">
          Freshly catalogued by AI
        </h2>
        {eligible.length > 1 && (
          <button
            type="button"
            className="text-xs font-medium text-brand-600 hover:text-brand-700 dark:text-brand-400 dark:hover:text-brand-300"
            onClick={next}
          >
            Next →
          </button>
        )}
      </div>

      <div key={book.id} className="card flex flex-col gap-4 p-4 sm:flex-row sm:p-5">
        <button
          type="button"
          className="mx-auto shrink-0 sm:mx-0"
          onClick={() => onOpenBook(book.id)}
        >
          <Cover
            token={token}
            driveId={book.id}
            isbn={book.isbn}
            title={book.title}
            author={book.author}
            size="lg"
          />
        </button>

        <div className="min-w-0 flex-1">
          <button type="button" className="text-left" onClick={() => onOpenBook(book.id)}>
            <h3 className="text-lg leading-tight font-semibold tracking-tight hover:text-brand-600 dark:hover:text-brand-400">
              {book.title}
            </h3>
          </button>
          <p className="mt-0.5 text-sm text-neutral-500">
            {book.author ?? 'Unknown author'}
            {book.series && (
              <>
                {' · '}
                {book.series}
                {book.seriesNumber ? ` #${book.seriesNumber}` : ''}
              </>
            )}
          </p>

          {tags.ageRating && (
            <span className="badge mt-2 bg-neutral-100 text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300">
              {tags.ageRating}
            </span>
          )}

          {tags.shortDescription && (
            <p className="mt-2 text-sm leading-relaxed text-neutral-600 dark:text-neutral-300">
              {tags.shortDescription}
            </p>
          )}

          <div className="mt-2 flex flex-wrap gap-1">
            {chipList(
              tags.genres,
              'bg-brand-50 text-brand-700 dark:bg-brand-950/60 dark:text-brand-300',
            )}
            {chipList(
              tags.moods,
              'bg-violet-50 text-violet-700 dark:bg-violet-950/50 dark:text-violet-300',
            )}
            {chipList(tags.themes, 'bg-sky-50 text-sky-700 dark:bg-sky-950/50 dark:text-sky-300')}
            {chipList(
              tags.representation,
              'bg-teal-50 text-teal-700 dark:bg-teal-950/50 dark:text-teal-300',
            )}
          </div>

          {tags.contentWarnings.length > 0 && (
            <details className="mt-2 text-xs text-neutral-400">
              <summary className="cursor-pointer select-none marker:text-neutral-300 hover:text-neutral-600 dark:hover:text-neutral-300">
                Content warnings ({tags.contentWarnings.length})
              </summary>
              <p className="mt-1 text-neutral-500">{tags.contentWarnings.join(' · ')}</p>
            </details>
          )}

          {tags.longSummary && (
            <details className="mt-3 text-sm">
              <summary className="cursor-pointer font-medium text-brand-600 select-none hover:text-brand-700 dark:text-brand-400 dark:hover:text-brand-300">
                Reveal full summary — contains spoilers
              </summary>
              <p className="mt-1.5 leading-relaxed text-neutral-600 dark:text-neutral-300">
                {tags.longSummary}
              </p>
            </details>
          )}

          <p className="mt-2 text-[11px] text-neutral-400">
            Read cover to cover by a local AI{tags.generatedAt && ` · ${timeAgo(tags.generatedAt)}`}
            {tags.confidenceNotes && ` · ${tags.confidenceNotes}`}
          </p>
        </div>
      </div>
    </section>
  )
}
