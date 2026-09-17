import { useState } from 'react'
import type { FileSummary } from '../types/files'
import type { CorrectReviewRequest } from '../types/reviews'

const INVALID_CHARS_RE = /[\\/:*?"<>|]/g

function sanitize(s: string): string {
  return s.replace(INVALID_CHARS_RE, ' ').trim() || 'Untitled'
}

// Mirrors organize_service.build_target_path.
function preview(title: string, author: string, series: string, num: string): string {
  const parts: string[] = []
  if (author.trim()) parts.push(sanitize(author))
  if (series.trim()) parts.push(sanitize(series))
  parts.push(title.trim() ? sanitize(title) : '(untitled)')
  return parts.join(' › ') + (series.trim() && num.trim() ? ` #${num.trim()}` : '')
}

// When provided, the form opens with these values instead of the file's
// stored ones — e.g. the deep re-check's suggested fix. A present `initial`
// fully specifies all four fields, so a null series here means "standalone"
// rather than "fall back to the stored series".
export interface CorrectFormInitial {
  title: string
  author: string | null
  series: string | null
  seriesNumber: number | null
}

export function CorrectFileForm({
  file,
  initial,
  initialNote,
  busy,
  error,
  onSubmit,
  onCancel,
}: {
  file: FileSummary
  initial?: CorrectFormInitial
  initialNote?: string
  busy: boolean
  error: string | null
  onSubmit: (body: CorrectReviewRequest) => void
  onCancel: () => void
}) {
  const [title, setTitle] = useState(initial?.title ?? file.book_title ?? '')
  const [author, setAuthor] = useState(
    (initial ? initial.author : file.book_author) ?? '',
  )
  const [series, setSeries] = useState((initial ? initial.series : file.book_series) ?? '')
  const [num, setNum] = useState(() => {
    const n = initial ? initial.seriesNumber : file.book_series_number
    return n !== null && n !== undefined ? String(n) : ''
  })

  return (
    <div className="card mt-2 space-y-2 p-3">
      {initialNote && <p className="text-xs text-amber-700 dark:text-amber-400">{initialNote}</p>}
      <label className="block text-xs text-neutral-500">
        Title
        <input
          className="field mt-1 w-full py-1 text-sm"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
        />
      </label>
      <label className="block text-xs text-neutral-500">
        Author
        <input
          className="field mt-1 w-full py-1 text-sm"
          value={author}
          onChange={(e) => setAuthor(e.target.value)}
        />
      </label>
      <div className="flex gap-2">
        <label className="block flex-1 text-xs text-neutral-500">
          Series (blank = standalone)
          <input
            className="field mt-1 w-full py-1 text-sm"
            value={series}
            onChange={(e) => setSeries(e.target.value)}
          />
        </label>
        <label className="block w-20 text-xs text-neutral-500">
          Book #
          <input
            className="field mt-1 w-full py-1 text-sm"
            value={num}
            onChange={(e) => setNum(e.target.value)}
          />
        </label>
      </div>
      <p className="text-xs text-neutral-400">
        Will re-file to: <span className="font-mono">{preview(title, author, series, num)}</span>
      </p>
      {error && <p className="text-xs text-red-600">{error}</p>}
      <div className="flex items-center gap-2 pt-1">
        <button
          className="btn btn-primary py-1.5"
          disabled={busy || title.trim() === ''}
          onClick={() =>
            onSubmit({
              title: title.trim(),
              author: author.trim() || null,
              series: series.trim() || null,
              series_number: series.trim() && num.trim() ? Number(num) : null,
            })
          }
        >
          {busy ? 'Saving…' : 'Save correction'}
        </button>
        <button
          className="btn btn-neutral py-1.5"
          onClick={onCancel}
        >
          Cancel
        </button>
        <span className="text-xs text-neutral-400">
          Then click <b>Organize</b> to move the file.
        </span>
      </div>
    </div>
  )
}
