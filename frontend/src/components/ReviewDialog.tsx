import { useState } from 'react'
import { ConfidenceBar } from './ConfidenceBar'
import type { CorrectReviewRequest, ReviewDetail } from '../types/reviews'

const STATUS_REASON_LABEL: Record<string, string> = {
  multi_parent: 'File is in multiple Drive folders — resolve in Drive before this can be organized.',
  no_parent: 'File has no Drive folder — resolve in Drive before this can be organized.',
  manual_drift: 'File was moved manually in Drive since it was last organized.',
  low_confidence: 'Identification confidence was too low to auto-organize.',
}

export function ReviewDialog({
  review,
  onApprove,
  onCorrect,
  onReject,
  busy,
}: {
  review: ReviewDetail
  onApprove: () => void
  onCorrect: (body: CorrectReviewRequest) => void
  onReject: () => void
  busy: boolean
}) {
  const [editing, setEditing] = useState(false)
  const [title, setTitle] = useState(review.proposed_title ?? '')
  const [author, setAuthor] = useState(review.proposed_author ?? '')
  const [series, setSeries] = useState((review.proposed_json.series as string | null) ?? '')
  const [seriesNumber, setSeriesNumber] = useState(
    review.proposed_json.series_number != null ? String(review.proposed_json.series_number) : '',
  )
  const [applyToSimilar, setApplyToSimilar] = useState(false)

  const structuralNote = review.status_reason ? STATUS_REASON_LABEL[review.status_reason] : null
  const proposedSeries = review.proposed_json.series as string | null
  const proposedSeriesNumber = review.proposed_json.series_number as number | null

  return (
    <div className="rounded border border-neutral-200 p-4 dark:border-neutral-800">
      <div className="flex items-start justify-between">
        <div>
          <h3 className="font-medium">{review.filename}</h3>
          <p className="text-sm text-neutral-500">
            Proposed: {review.proposed_title ?? '(none)'}
            {review.proposed_author ? ` by ${review.proposed_author}` : ''}
          </p>
          {proposedSeries && (
            <p className="text-sm text-neutral-500">
              Series: {proposedSeries}
              {proposedSeriesNumber != null ? ` #${proposedSeriesNumber}` : ''}
            </p>
          )}
        </div>
        <ConfidenceBar value={review.computed_confidence} />
      </div>

      {structuralNote && (
        <p className="mt-3 rounded bg-amber-100 px-2 py-1 text-xs text-amber-800 dark:bg-amber-900/40 dark:text-amber-300">
          {structuralNote}
        </p>
      )}

      {review.reasoning_summary && (
        <p className="mt-3 text-sm text-neutral-600 dark:text-neutral-400">{review.reasoning_summary}</p>
      )}

      <div className="mt-4">
        <h4 className="text-xs font-medium uppercase text-neutral-400">Evidence</h4>
        <ul className="mt-1 space-y-1 text-sm">
          {review.evidence.map((item, i) => (
            <li key={i} className="flex gap-2">
              <span className="w-24 shrink-0 text-neutral-400">{item.field_name}</span>
              <span className="truncate">{item.value}</span>
            </li>
          ))}
        </ul>
      </div>

      {review.candidates.length > 0 && (
        <div className="mt-4">
          <h4 className="text-xs font-medium uppercase text-neutral-400">Provider candidates</h4>
          <ul className="mt-1 space-y-1 text-sm">
            {review.candidates.map((c, i) => (
              <li key={i} className="text-neutral-600 dark:text-neutral-400">
                [{c.source}] {c.title ?? '(no title)'} — {c.author ?? '(no author)'}
                {c.series ? ` — ${c.series}` : ''}
              </li>
            ))}
          </ul>
        </div>
      )}

      {!editing ? (
        <div className="mt-5 flex gap-2">
          <button
            className="rounded bg-neutral-900 px-3 py-1.5 text-sm text-white disabled:opacity-50 dark:bg-neutral-100 dark:text-neutral-900"
            disabled={busy}
            onClick={onApprove}
          >
            Approve
          </button>
          <button
            className="rounded border border-neutral-300 px-3 py-1.5 text-sm disabled:opacity-50 dark:border-neutral-700"
            disabled={busy}
            onClick={() => setEditing(true)}
          >
            Edit
          </button>
          <button
            className="rounded border border-red-300 px-3 py-1.5 text-sm text-red-700 disabled:opacity-50 dark:border-red-800 dark:text-red-400"
            disabled={busy}
            onClick={onReject}
          >
            Reject
          </button>
        </div>
      ) : (
        <div className="mt-5 space-y-2 rounded border border-neutral-200 p-3 dark:border-neutral-800">
          <label className="block text-xs text-neutral-500">
            Title
            <input
              className="mt-1 w-full rounded border border-neutral-300 px-2 py-1 text-sm dark:border-neutral-700 dark:bg-neutral-900"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
            />
          </label>
          <label className="block text-xs text-neutral-500">
            Author
            <input
              className="mt-1 w-full rounded border border-neutral-300 px-2 py-1 text-sm dark:border-neutral-700 dark:bg-neutral-900"
              value={author}
              onChange={(e) => setAuthor(e.target.value)}
            />
          </label>
          <div className="flex gap-2">
            <label className="block flex-1 text-xs text-neutral-500">
              Series
              <input
                className="mt-1 w-full rounded border border-neutral-300 px-2 py-1 text-sm dark:border-neutral-700 dark:bg-neutral-900"
                value={series}
                onChange={(e) => setSeries(e.target.value)}
              />
            </label>
            <label className="block w-24 text-xs text-neutral-500">
              #
              <input
                className="mt-1 w-full rounded border border-neutral-300 px-2 py-1 text-sm dark:border-neutral-700 dark:bg-neutral-900"
                value={seriesNumber}
                onChange={(e) => setSeriesNumber(e.target.value)}
              />
            </label>
          </div>
          <label className="flex items-center gap-2 text-xs text-neutral-500">
            <input
              type="checkbox"
              checked={applyToSimilar}
              onChange={(e) => setApplyToSimilar(e.target.checked)}
            />
            Apply this author/series correction to similar files in the future
          </label>
          <div className="flex gap-2 pt-1">
            <button
              className="rounded bg-neutral-900 px-3 py-1.5 text-sm text-white disabled:opacity-50 dark:bg-neutral-100 dark:text-neutral-900"
              disabled={busy || title.trim() === ''}
              onClick={() =>
                onCorrect({
                  title: title.trim(),
                  author: author.trim() || null,
                  series: series.trim() || null,
                  series_number: seriesNumber.trim() ? Number(seriesNumber) : null,
                  apply_to_similar: applyToSimilar,
                })
              }
            >
              Save correction
            </button>
            <button
              className="rounded border border-neutral-300 px-3 py-1.5 text-sm dark:border-neutral-700"
              onClick={() => setEditing(false)}
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
