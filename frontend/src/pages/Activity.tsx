import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, ApiError } from '../services/api'

export function Activity() {
  const queryClient = useQueryClient()
  const [error, setError] = useState<string | null>(null)

  const operations = useQuery({ queryKey: ['operations'], queryFn: api.listOperations })

  const undo = useMutation({
    mutationFn: (id: number) => api.undoOperation(id),
    onSuccess: () => {
      setError(null)
      queryClient.invalidateQueries({ queryKey: ['operations'] })
    },
    onError: (err: unknown) => setError(err instanceof ApiError ? err.message : 'Failed to undo.'),
  })

  return (
    <div className="p-6">
      <h1 className="text-xl font-semibold">Activity</h1>
      <p className="mt-1 text-sm text-neutral-500">
        Every move/rename the app has made — dry runs included, clearly marked.
      </p>

      {error && <p className="mt-3 text-sm text-red-600">{error}</p>}

      <ul className="mt-4 divide-y divide-neutral-100 text-sm dark:divide-neutral-800">
        {operations.data?.map((op) => (
          <li key={op.id} className="py-3">
            <div className="flex items-center justify-between">
              <span className="font-medium">{op.filename}</span>
              <div className="flex items-center gap-2">
                {op.dry_run && (
                  <span className="rounded bg-amber-100 px-2 py-0.5 text-xs text-amber-800 dark:bg-amber-900/40 dark:text-amber-300">
                    dry run
                  </span>
                )}
                <span
                  className={`rounded px-2 py-0.5 text-xs ${
                    op.status === 'undone'
                      ? 'bg-neutral-200 text-neutral-600 dark:bg-neutral-800 dark:text-neutral-400'
                      : 'bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300'
                  }`}
                >
                  {op.status}
                </span>
              </div>
            </div>
            <p className="text-xs text-neutral-500">
              {op.original_parent_id ?? '(unknown)'}/{op.original_name ?? op.filename} →{' '}
              {op.new_parent_id ?? '(unknown)'}/{op.new_name ?? op.filename}
              {op.confidence !== null ? ` — confidence ${op.confidence}` : ''}
            </p>
            <p className="text-xs text-neutral-400">{new Date(op.timestamp).toLocaleString()}</p>

            {!op.dry_run && op.status === 'done' && (
              <button
                className="mt-2 rounded border border-neutral-300 px-2 py-1 text-xs disabled:opacity-50 dark:border-neutral-700"
                disabled={undo.isPending}
                onClick={() => undo.mutate(op.id)}
              >
                Undo
              </button>
            )}
          </li>
        ))}
        {operations.data?.length === 0 && (
          <li className="py-4 text-neutral-400">Nothing has been organized yet.</li>
        )}
      </ul>
    </div>
  )
}
