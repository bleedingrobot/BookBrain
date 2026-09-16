import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, ApiError } from '../services/api'
import type { SimilarNameCluster } from '../types/libraryAudit'
import type { SeriesMergeResult } from '../types/seriesMerge'

export function SeriesMergePanel({ cluster }: { cluster: SimilarNameCluster }) {
  const queryClient = useQueryClient()
  const seriesIds = [...cluster.members.map((m) => m.id)].sort((a, b) => a - b)
  const clusterKey = seriesIds.join(',')

  const [expanded, setExpanded] = useState(false)
  const [confirmingApply, setConfirmingApply] = useState(false)
  const [applyError, setApplyError] = useState<string | null>(null)
  const [applyResult, setApplyResult] = useState<SeriesMergeResult | null>(null)

  const proposal = useQuery({
    queryKey: ['series-merge-proposal', clusterKey],
    queryFn: () => api.investigateSeriesMerge(seriesIds),
    enabled: expanded,
  })

  const apply = useMutation({
    mutationFn: (canonicalName: string) =>
      api.applySeriesMerge(seriesIds, canonicalName, proposal.data?.excluded_series_names ?? []),
    onSuccess: (result) => {
      setConfirmingApply(false)
      setApplyError(null)
      setApplyResult(result)
      queryClient.invalidateQueries({ queryKey: ['library-audit'] })
      queryClient.invalidateQueries({ queryKey: ['files'] })
      queryClient.invalidateQueries({ queryKey: ['series-merge-proposal', clusterKey] })
    },
    onError: (err: unknown) =>
      setApplyError(err instanceof ApiError ? err.message : 'Failed to apply the fix.'),
  })

  return (
    <div className="mt-2">
      <button
        className="rounded border border-neutral-300 px-2 py-1 text-xs dark:border-neutral-700"
        onClick={() => setExpanded((v) => !v)}
      >
        {expanded ? 'Hide' : 'Investigate'}
      </button>

      {expanded && (
        <div className="mt-2 space-y-2 rounded border border-neutral-200 p-3 text-sm dark:border-neutral-800">
          {proposal.isLoading && <p className="text-neutral-500">Asking Claude to compare these…</p>}
          {proposal.isError && <p className="text-red-600">Failed to investigate this cluster.</p>}

          {proposal.data && (
            <>
              {!proposal.data.is_same_series && (
                <p className="text-amber-600 dark:text-amber-400">
                  Claude isn't confident these are the same series — review manually before merging.
                </p>
              )}
              <p>
                Proposed canonical name:{' '}
                <span className="font-medium">{proposal.data.canonical_series_name}</span>{' '}
                <span className="text-xs text-neutral-500">({proposal.data.confidence}% confidence)</span>
              </p>
              <p className="text-neutral-600 dark:text-neutral-400">{proposal.data.explanation}</p>
              {proposal.data.warnings.length > 0 && (
                <ul className="list-disc pl-5 text-xs text-amber-600 dark:text-amber-400">
                  {proposal.data.warnings.map((w, i) => (
                    <li key={i}>{w}</li>
                  ))}
                </ul>
              )}

              <ul className="space-y-1 text-xs text-neutral-500">
                {proposal.data.series.map((s) => {
                  const excluded = proposal.data.excluded_series_names.includes(s.name)
                  return (
                    <li key={s.id}>
                      <span className="font-medium">{s.name}</span>
                      {excluded && (
                        <span className="text-amber-600 dark:text-amber-400"> (not merged)</span>
                      )}
                      : {s.books.map((b) => b.canonical_title).join(', ')}
                    </li>
                  )
                })}
              </ul>

              {!applyResult && proposal.data.is_same_series && (
                <>
                  {(proposal.data.plan.moves.length > 0 || proposal.data.plan.series_to_delete.length > 0) && (
                    <div className="rounded border border-neutral-200 bg-neutral-50 p-2 dark:border-neutral-800 dark:bg-neutral-900/40">
                      <p className="font-medium text-neutral-600 dark:text-neutral-300">
                        Exactly what "Apply fix" will do:
                      </p>
                      <ul className="mt-1 list-disc space-y-1 pl-4 text-xs text-neutral-600 dark:text-neutral-400">
                        {proposal.data.plan.moves.map((m, i) => (
                          <li key={i}>
                            "{m.current_filename}" ({m.book_title}, currently under "{m.from_series_name}
                            ") gets moved into "{m.new_folder_path}/", renamed to "{m.new_filename}", and
                            its book record repointed to "{proposal.data.canonical_series_name}".
                          </li>
                        ))}
                        {proposal.data.plan.series_to_delete.map((name) => (
                          <li key={name}>The now-empty "{name}" series gets deleted.</li>
                        ))}
                      </ul>
                      <p className="mt-1 text-xs text-neutral-500">
                        {proposal.data.plan.moves.length} Operation(s) get logged — each undoable
                        individually from Activity.
                      </p>
                    </div>
                  )}

                  {applyError && <p className="text-red-600">{applyError}</p>}
                  {!confirmingApply ? (
                    <button
                      className="rounded bg-neutral-900 px-3 py-1.5 text-xs text-white dark:bg-neutral-100 dark:text-neutral-900"
                      onClick={() => setConfirmingApply(true)}
                    >
                      Apply fix
                    </button>
                  ) : (
                    <div className="space-y-2 rounded border border-amber-300 p-3 dark:border-amber-800">
                      <p className="text-amber-700 dark:text-amber-400">Go ahead with exactly the plan shown above?</p>
                      <div className="flex gap-2">
                        <button
                          className="rounded bg-amber-600 px-3 py-1.5 text-xs text-white disabled:opacity-50"
                          disabled={apply.isPending}
                          onClick={() => apply.mutate(proposal.data.canonical_series_name)}
                        >
                          Yes, apply fix
                        </button>
                        <button
                          className="rounded border border-neutral-300 px-3 py-1.5 text-xs dark:border-neutral-700"
                          onClick={() => setConfirmingApply(false)}
                        >
                          Cancel
                        </button>
                      </div>
                    </div>
                  )}
                </>
              )}

              {applyResult && (
                <p className="text-xs text-neutral-500">
                  Merged into "{applyResult.canonical_series_name}": {applyResult.moved_files} moved,{' '}
                  {applyResult.repointed_books} book(s) repointed
                  {applyResult.failed_files.length > 0 && `, ${applyResult.failed_files.length} file(s) failed`}
                  {applyResult.skipped_books.length > 0 &&
                    `, ${applyResult.skipped_books.length} book(s) left unmerged`}
                  .
                  {applyResult.failed_files.length > 0 && ' Re-investigate and apply again to retry the rest.'}
                </p>
              )}
            </>
          )}
        </div>
      )}
    </div>
  )
}
