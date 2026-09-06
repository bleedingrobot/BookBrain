import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, ApiError } from '../services/api'
import type { FileSummary } from '../types/files'
import type { CorrectReviewRequest } from '../types/reviews'
import type { HeldFileItem, RecentlyOrganizedItem } from '../types/library'
import { ConfidenceBar } from './ConfidenceBar'
import { CorrectFileForm } from './CorrectFileForm'

const SINCE_OPTIONS: { label: string; value: string }[] = [
  { label: '24h', value: '24h' },
  { label: '48h', value: '48h' },
  { label: '7d', value: '7d' },
]

// CorrectFileForm only reads id + the book_* fields.
function asFileSummary(item: RecentlyOrganizedItem | HeldFileItem): FileSummary {
  return {
    id: item.file_id,
    filename: item.filename,
    status: 'operation_id' in item ? 'organised' : 'held',
    status_reason: null,
    book_title: item.title,
    book_author: item.author,
    book_series: item.series,
    book_series_number: item.series_number,
    computed_confidence: item.confidence,
    ai_reasoning: null,
    quality_score: null,
    discovered_at: '',
  }
}

function bookLine(item: { title: string | null; author: string | null; series: string | null; series_number: number | null; filename: string }) {
  if (!item.title) return item.filename
  const bits = [item.author, item.title].filter(Boolean)
  let line = bits.join(' — ')
  if (item.series) line += ` (${item.series}${item.series_number != null ? ` #${item.series_number}` : ''})`
  return line
}

export function RecentlyOrganized() {
  const queryClient = useQueryClient()
  const [since, setSince] = useState('48h')
  const [correctingId, setCorrectingId] = useState<number | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)

  const query = useQuery({
    queryKey: ['recently-organized', since],
    queryFn: () => api.getRecentlyOrganized(since),
  })

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['recently-organized'] })
    queryClient.invalidateQueries({ queryKey: ['files'] })
    queryClient.invalidateQueries({ queryKey: ['reviews', 'pending'] })
  }

  const confirm = useMutation({
    mutationFn: (id: number) => api.confirmFile(id),
    onSuccess: invalidate,
    onError: (err: unknown) =>
      setActionError(err instanceof ApiError ? err.message : 'Failed to confirm.'),
  })

  const correct = useMutation({
    mutationFn: ({ id, body }: { id: number; body: CorrectReviewRequest }) =>
      api.correctFile(id, body),
    onSuccess: () => {
      setCorrectingId(null)
      setActionError(null)
      invalidate()
    },
    onError: (err: unknown) =>
      setActionError(err instanceof ApiError ? err.message : 'Failed to save correction.'),
  })

  const data = query.data
  const organized = data?.organized ?? []
  const held = data?.held ?? []
  const holdOn = (data?.hold_hours ?? 0) > 0

  if (query.isLoading) return null
  if (!query.isError && organized.length === 0 && held.length === 0) return null

  const renderRow = (item: RecentlyOrganizedItem | HeldFileItem, isHeld: boolean) => {
    const recent = isHeld ? null : (item as RecentlyOrganizedItem)
    return (
      <li key={item.file_id} className="py-2.5">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm">{bookLine(item)}</p>
            <p className="mt-0.5 text-xs text-neutral-500">{item.evidence_summary}</p>
            <div className="mt-1 flex flex-wrap items-center gap-3">
              <ConfidenceBar value={item.confidence} />
              {isHeld ? (
                <span className="text-xs text-amber-600 dark:text-amber-400">
                  waits until {new Date((item as HeldFileItem).eligible_at).toLocaleString()}
                </span>
              ) : recent && recent.current_status !== 'organised' ? (
                <span className="text-xs text-amber-600 dark:text-amber-400">
                  pulled back to {recent.current_status}
                </span>
              ) : (
                <span className="text-xs text-neutral-400">
                  {new Date((item as RecentlyOrganizedItem).organized_at).toLocaleString()}
                </span>
              )}
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {!isHeld && recent?.confirmed ? (
              <span className="text-xs text-green-700 dark:text-green-500">✓ confirmed</span>
            ) : (
              <button
                className="rounded border border-neutral-300 px-2 py-1 text-xs disabled:opacity-50 dark:border-neutral-700"
                disabled={confirm.isPending}
                onClick={() => {
                  setActionError(null)
                  confirm.mutate(item.file_id)
                }}
              >
                Confirm
              </button>
            )}
            <button
              className="rounded border border-neutral-300 px-2 py-1 text-xs dark:border-neutral-700"
              onClick={() => {
                setActionError(null)
                setCorrectingId(correctingId === item.file_id ? null : item.file_id)
              }}
            >
              Correct
            </button>
          </div>
        </div>
        {correctingId === item.file_id && (
          <CorrectFileForm
            file={asFileSummary(item)}
            busy={correct.isPending}
            error={actionError}
            onSubmit={(body) => correct.mutate({ id: item.file_id, body })}
            onCancel={() => setCorrectingId(null)}
          />
        )}
      </li>
    )
  }

  return (
    <div className="mt-6 rounded border border-neutral-200 p-4 dark:border-neutral-800">
      <div className="flex items-center justify-between gap-4">
        <h2 className="text-sm font-medium text-neutral-500">
          Recently auto-organized{organized.length ? ` (${organized.length})` : ''}
        </h2>
        <div className="flex gap-1 text-xs">
          {SINCE_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              className={`rounded px-2 py-0.5 ${
                since === opt.value
                  ? 'bg-neutral-900 text-white dark:bg-neutral-100 dark:text-neutral-900'
                  : 'text-neutral-400'
              }`}
              onClick={() => setSince(opt.value)}
            >
              {opt.label}
            </button>
          ))}
        </div>
      </div>

      <p className="mt-1 text-xs text-neutral-400">
        Books moved into the library without review. Glance down the list; hit{' '}
        <b>Confirm</b> if it's right, <b>Correct</b> if it isn't.
      </p>

      {query.isError && (
        <p className="mt-2 text-sm text-red-600">Couldn't load the list.</p>
      )}
      {actionError && !correctingId && (
        <p className="mt-2 text-sm text-red-600">{actionError}</p>
      )}

      {organized.length > 0 ? (
        <ul className="mt-2 divide-y divide-neutral-100 dark:divide-neutral-800">
          {organized.map((item) => renderRow(item, false))}
        </ul>
      ) : (
        <p className="mt-2 text-xs text-neutral-400">Nothing auto-organized in this window.</p>
      )}

      {holdOn && (
        <div className="mt-4 border-t border-neutral-200 pt-3 dark:border-neutral-800">
          <h3 className="text-xs font-medium text-neutral-500">
            Held, waiting {data!.hold_hours}h before moving{held.length ? ` (${held.length})` : ''}
          </h3>
          {held.length > 0 ? (
            <ul className="mt-2 divide-y divide-neutral-100 dark:divide-neutral-800">
              {held.map((item) => renderRow(item, true))}
            </ul>
          ) : (
            <p className="mt-2 text-xs text-neutral-400">Nothing held right now.</p>
          )}
        </div>
      )}
    </div>
  )
}
