import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type ReactNode } from 'react'
import type { DuplicateGroup } from '../types/duplicates'
import { api, ApiError } from '../services/api'

const REASON_LABEL: Record<string, string> = {
  previously_rejected: 'a previously rejected identification',
  same_book: 'a different edition/upload of a book already in your library',
}

function GroupRow({ group, children }: { group: DuplicateGroup; children?: ReactNode }) {
  return (
    <li className="py-3">
      <div className="flex items-center justify-between gap-4">
        <span className="font-medium">{group.duplicate_filename}</span>
        <div className="flex shrink-0 items-center gap-3">
          {group.quality_score !== null && (
            <span className="text-xs text-neutral-400">quality {group.quality_score}</span>
          )}
          {children}
        </div>
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
  )
}

export function Duplicates({ embedded = false }: { embedded?: boolean } = {}) {
  const queryClient = useQueryClient()
  const [confirmingClear, setConfirmingClear] = useState(false)
  const [clearError, setClearError] = useState<string | null>(null)
  const [clearResult, setClearResult] = useState<{ cleared: number; failed: number } | null>(null)
  const [rowError, setRowError] = useState<string | null>(null)

  const duplicates = useQuery({ queryKey: ['duplicates'], queryFn: api.listDuplicates })

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['duplicates'] })
    queryClient.invalidateQueries({ queryKey: ['files'] })
  }

  const clearDuplicates = useMutation({
    mutationFn: api.clearDuplicates,
    onSuccess: (result) => {
      setConfirmingClear(false)
      setClearError(null)
      setClearResult(result)
      invalidate()
    },
    onError: (err: unknown) =>
      setClearError(err instanceof ApiError ? err.message : 'Failed to clear duplicates.'),
  })

  const rowError_ = (err: unknown) =>
    setRowError(err instanceof ApiError ? err.message : 'That action failed.')

  const trashOne = useMutation({
    mutationFn: (fileId: number) => api.clearOneDuplicate(fileId),
    onSuccess: () => {
      setRowError(null)
      invalidate()
    },
    onError: rowError_,
  })

  const unflagOne = useMutation({
    mutationFn: (fileId: number) => api.unflagDuplicate(fileId),
    onSuccess: () => {
      setRowError(null)
      invalidate()
    },
    onError: rowError_,
  })

  const all = duplicates.data ?? []
  const sameBook = all.filter((g) => g.status_reason === 'same_book')
  const exactContent = all.filter((g) => g.status_reason !== 'same_book')

  return (
    <div className={embedded ? '' : 'p-6'}>
      <div className="flex items-start justify-between gap-4">
        {!embedded && (
          <div>
            <h1 className="text-xl font-semibold">Duplicates</h1>
            <p className="mt-1 max-w-xl text-sm text-neutral-500">
              Extra copies of books already in your library. Byte-identical copies (matched by
              content hash) can be cleared in bulk. "Same book" copies were matched by their
              resolved identification, not their bytes — check each one before removing it.
            </p>
          </div>
        )}

        {exactContent.length > 0 && (
          <div className="shrink-0 text-right">
            <button
              className="rounded border border-red-300 px-3 py-1.5 text-sm text-red-700 disabled:opacity-50 dark:border-red-800 dark:text-red-400"
              onClick={() => setConfirmingClear(true)}
            >
              Clear exact-content duplicates
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
            This moves every byte-identical duplicate file straight to Drive's Trash (recoverable
            there, not permanently deleted) and removes its record from the app. The primary copy
            each duplicate matches is never touched, and "same book" copies below are left alone.
          </p>
          {clearError && <p className="text-red-600">{clearError}</p>}
          <div className="flex gap-2">
            <button
              className="rounded bg-red-600 px-3 py-1.5 text-white disabled:opacity-50"
              disabled={clearDuplicates.isPending}
              onClick={() => clearDuplicates.mutate()}
            >
              Yes, clear them
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

      {rowError && <p className="mt-3 text-sm text-red-600">{rowError}</p>}

      {duplicates.data?.length === 0 && (
        <p className="mt-4 text-sm text-neutral-400">No duplicates found.</p>
      )}

      {exactContent.length > 0 && (
        <section className="mt-4">
          <h2 className="text-sm font-medium text-neutral-500">Exact-content duplicates</h2>
          <ul className="divide-y divide-neutral-100 text-sm dark:divide-neutral-800">
            {exactContent.map((group) => (
              <GroupRow key={group.duplicate_file_id} group={group} />
            ))}
          </ul>
        </section>
      )}

      {sameBook.length > 0 && (
        <section className="mt-6">
          <h2 className="text-sm font-medium text-neutral-500">Same book, different file</h2>
          <p className="mt-1 max-w-xl text-xs text-neutral-400">
            These were matched to a book already in your library by identification, not by content.
            If that's right, trash the extra copy. If it's actually a different book that was
            misidentified, choose "Not a duplicate" — it'll be split off and re-processed.
          </p>
          <ul className="divide-y divide-neutral-100 text-sm dark:divide-neutral-800">
            {sameBook.map((group) => (
              <GroupRow key={group.duplicate_file_id} group={group}>
                <button
                  className="rounded border border-red-300 px-2 py-1 text-xs text-red-700 disabled:opacity-50 dark:border-red-800 dark:text-red-400"
                  disabled={trashOne.isPending || unflagOne.isPending}
                  onClick={() => trashOne.mutate(group.duplicate_file_id)}
                >
                  Trash this copy
                </button>
                <button
                  className="rounded border border-neutral-300 px-2 py-1 text-xs text-neutral-600 disabled:opacity-50 dark:border-neutral-700 dark:text-neutral-300"
                  disabled={trashOne.isPending || unflagOne.isPending}
                  onClick={() => unflagOne.mutate(group.duplicate_file_id)}
                >
                  Not a duplicate
                </button>
              </GroupRow>
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}
