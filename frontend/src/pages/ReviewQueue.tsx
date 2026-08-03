import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, ApiError } from '../services/api'
import { ConfidenceBar } from '../components/ConfidenceBar'
import { ReviewDialog } from '../components/ReviewDialog'
import type { CorrectReviewRequest } from '../types/reviews'

export function ReviewQueue({ embedded = false }: { embedded?: boolean } = {}) {
  const queryClient = useQueryClient()
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)

  const reviews = useQuery({ queryKey: ['reviews', 'pending'], queryFn: () => api.listReviews('pending') })
  const detail = useQuery({
    queryKey: ['review', selectedId],
    queryFn: () => api.getReview(selectedId as number),
    enabled: selectedId !== null,
  })

  function afterResolved(resolvedId: number) {
    const list = reviews.data ?? []
    const next = list[list.findIndex((r) => r.id === resolvedId) + 1]
    setSelectedId(next?.id ?? null)
    setActionError(null)
    queryClient.invalidateQueries({ queryKey: ['reviews', 'pending'] })
    // approve/correct move the file to inbox status — anything watching
    // file counts (the Dashboard's organize-ready prompt, the Library
    // page) needs to see that too, not just the review list shrink.
    queryClient.invalidateQueries({ queryKey: ['files'] })
  }

  const approve = useMutation({
    mutationFn: (id: number) => api.approveReview(id),
    onSuccess: (_data, id) => afterResolved(id),
    onError: (err: unknown) =>
      setActionError(err instanceof ApiError ? err.message : 'Failed to approve.'),
  })
  const correct = useMutation({
    mutationFn: ({ id, body }: { id: number; body: CorrectReviewRequest }) => api.correctReview(id, body),
    onSuccess: (_data, { id }) => afterResolved(id),
    onError: (err: unknown) =>
      setActionError(err instanceof ApiError ? err.message : 'Failed to save correction.'),
  })
  const reject = useMutation({
    mutationFn: (id: number) => api.rejectReview(id),
    onSuccess: (_data, id) => afterResolved(id),
    onError: (err: unknown) =>
      setActionError(err instanceof ApiError ? err.message : 'Failed to reject.'),
  })

  const busy = approve.isPending || correct.isPending || reject.isPending

  return (
    <div className={embedded ? 'grid grid-cols-1 gap-6 md:grid-cols-2' : 'grid grid-cols-1 gap-6 p-6 md:grid-cols-2'}>
      <div>
        {!embedded && (
          <>
            <h1 className="text-xl font-semibold">Review Queue</h1>
            <p className="mt-1 text-sm text-neutral-500">
              Files that need a human decision — low confidence (any evidence strength) or a
              Drive folder conflict. Check the confidence number: thin evidence can still be
              correct.
            </p>
          </>
        )}

        <ul className="mt-4 divide-y divide-neutral-100 dark:divide-neutral-800">
          {reviews.data?.map((r) => (
            <li key={r.id}>
              <button
                className={`w-full py-2 text-left text-sm ${
                  selectedId === r.id ? 'text-neutral-900 dark:text-neutral-100' : 'text-neutral-600 dark:text-neutral-400'
                }`}
                onClick={() => setSelectedId(r.id)}
              >
                <div className="flex items-center justify-between">
                  <span className="truncate">{r.filename}</span>
                  <ConfidenceBar value={r.computed_confidence} />
                </div>
                <div className="text-xs text-neutral-400">
                  {r.proposed_title ?? '(no proposed title)'}
                  {r.status_reason ? ` — ${r.status_reason}` : ''}
                </div>
              </button>
            </li>
          ))}
          {reviews.data?.length === 0 && (
            <li className="py-4 text-sm text-neutral-400">Nothing waiting for review.</li>
          )}
        </ul>
      </div>

      <div>
        {actionError && <p className="mb-3 text-sm text-red-600">{actionError}</p>}
        {detail.data ? (
          <ReviewDialog
            review={detail.data}
            busy={busy}
            onApprove={() => approve.mutate(detail.data!.id)}
            onCorrect={(body) => correct.mutate({ id: detail.data!.id, body })}
            onReject={() => reject.mutate(detail.data!.id)}
          />
        ) : (
          <p className="text-sm text-neutral-400">Select a file to review.</p>
        )}
      </div>
    </div>
  )
}
