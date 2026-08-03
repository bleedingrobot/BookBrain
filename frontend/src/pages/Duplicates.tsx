import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api, ApiError } from '../services/api'

const REASON_LABEL: Record<string, string> = {
  previously_rejected: 'a previously rejected identification',
}

export function Duplicates({ embedded = false }: { embedded?: boolean } = {}) {
  const queryClient = useQueryClient()
  const [confirmingClear, setConfirmingClear] = useState(false)
  const [clearError, setClearError] = useState<string | null>(null)
  const [clearResult, setClearResult] = useState<{ cleared: number; failed: number } | null>(null)

  const duplicates = useQuery({ queryKey: ['duplicates'], queryFn: api.listDuplicates })

  const clearDuplicates = useMutation({
    mutationFn: api.clearDuplicates,
    onSuccess: (result) => {
      setConfirmingClear(false)
      setClearError(null)
      setClearResult(result)
      queryClient.invalidateQueries({ queryKey: ['duplicates'] })
      queryClient.invalidateQueries({ queryKey: ['files'] })
    },
    onError: (err: unknown) =>
      setClearError(err instanceof ApiError ? err.message : 'Failed to clear duplicates.'),
  })

  return (
    <div className={embedded ? '' : 'p-6'}>
      <div className="flex items-start justify-between gap-4">
        {!embedded && (
          <div>
            <h1 className="text-xl font-semibold">Duplicates</h1>
            <p className="mt-1 text-sm text-neutral-500">
              Files whose content exactly matches another file already in your Drive — detected
              by content hash, not filename.
            </p>
          </div>
        )}

        {duplicates.data && duplicates.data.length > 0 && (
          <div className="shrink-0 text-right">
            <button
              className="rounded border border-red-300 px-3 py-1.5 text-sm text-red-700 disabled:opacity-50 dark:border-red-800 dark:text-red-400"
              onClick={() => setConfirmingClear(true)}
            >
              Clear duplicates
            </button>
            {clearResult && (
              <p className="mt-1 text-xs text-neutral-500">
                {clearResult.cleared} cleared
                {clearResult.failed > 0 ? `, ${clearResult.failed} failed` : ''}
              </p>
            )}
          </div>
        )}
      </div>

      {confirmingClear && (
        <div className="mt-4 max-w-lg space-y-2 rounded border border-red-300 p-3 text-sm dark:border-red-800">
          <p className="text-red-700 dark:text-red-400">
            This moves every duplicate file straight to Drive's Trash (recoverable there, not
            permanently deleted) and removes its record from the app. The primary copy each
            duplicate matches is never touched.
          </p>
          {clearError && <p className="text-red-600">{clearError}</p>}
          <div className="flex gap-2">
            <button
              className="rounded bg-red-600 px-3 py-1.5 text-white disabled:opacity-50"
              disabled={clearDuplicates.isPending}
              onClick={() => clearDuplicates.mutate()}
            >
              Yes, clear duplicates
            </button>
            <button
              className="rounded border border-neutral-300 px-3 py-1.5 dark:border-neutral-700"
              onClick={() => setConfirmingClear(false)}
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      <ul className="mt-4 divide-y divide-neutral-100 text-sm dark:divide-neutral-800">
        {duplicates.data?.map((group) => (
          <li key={group.duplicate_file_id} className="py-3">
            <div className="flex items-center justify-between">
              <span className="font-medium">{group.duplicate_filename}</span>
              {group.quality_score !== null && (
                <span className="text-xs text-neutral-400">quality {group.quality_score}</span>
              )}
            </div>
            <p className="text-xs text-neutral-500">
              {group.primary_filename ? (
                <>
                  Duplicate of <span className="font-medium">{group.primary_filename}</span>
                </>
              ) : (
                <span className="italic">Duplicate of an unknown file (primary not found)</span>
              )}
              {group.status_reason && REASON_LABEL[group.status_reason] && (
                <span className="text-amber-600 dark:text-amber-400">
                  {' '}
                  — {REASON_LABEL[group.status_reason]}
                </span>
              )}
            </p>
          </li>
        ))}
        {duplicates.data?.length === 0 && (
          <li className="py-4 text-neutral-400">No duplicates found.</li>
        )}
      </ul>
    </div>
  )
}
