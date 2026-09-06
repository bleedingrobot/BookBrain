import { useState, type ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { SeriesMergePanel } from '../components/SeriesMergePanel'
import { ReidentAuditPanel } from '../components/ReidentAuditPanel'
import type { AuditClusterKind, SimilarNameCluster } from '../types/libraryAudit'
import { api } from '../services/api'

function ClusterList({
  clusters,
  noun,
  kind,
  renderActions,
}: {
  clusters: SimilarNameCluster[]
  noun: string
  kind: AuditClusterKind
  renderActions?: (cluster: SimilarNameCluster) => ReactNode
}) {
  const queryClient = useQueryClient()
  const dismiss = useMutation({
    mutationFn: (memberIds: number[]) => api.dismissAuditCluster(kind, memberIds),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['library-audit'] })
      queryClient.invalidateQueries({ queryKey: ['dismissed-audit-clusters'] })
    },
  })

  if (clusters.length === 0) {
    return <p className="mt-2 text-sm text-neutral-400">No likely-duplicate {noun} found.</p>
  }

  return (
    <ul className="mt-2 space-y-3">
      {clusters.map((cluster) => {
        const memberIds = cluster.members.map((m) => m.id)
        // Keyed by the cluster's actual member ids, not array position —
        // otherwise, once a cluster earlier in the list disappears (e.g.
        // merged away), everything below it shifts index and React reuses
        // that old component slot — including its stale internal state
        // (like a previous cluster's "Merged into ..." result) — for
        // whatever cluster now lands on that index, rather than mounting a
        // fresh one.
        const clusterKey = [...memberIds].sort((a, b) => a - b).join(',')
        return (
          <li
            key={clusterKey}
            className="rounded border border-amber-300 p-3 text-sm dark:border-amber-800"
          >
            <ul className="space-y-1">
              {cluster.members.map((member) => (
                <li key={member.id} className="flex items-center justify-between gap-4">
                  <span className="font-medium">{member.name}</span>
                  <span className="shrink-0 text-xs text-neutral-500">
                    {member.book_count} book{member.book_count === 1 ? '' : 's'}, {member.file_count}{' '}
                    file{member.file_count === 1 ? '' : 's'}
                  </span>
                </li>
              ))}
            </ul>
            <div className="mt-2 flex items-center gap-2">
              {renderActions?.(cluster)}
              <button
                className="rounded border border-neutral-300 px-2 py-1 text-xs text-neutral-500 disabled:opacity-50 dark:border-neutral-700"
                disabled={dismiss.isPending}
                onClick={() => dismiss.mutate(memberIds)}
                title="Already reviewed this one — stop flagging it"
              >
                Dismiss
              </button>
            </div>
          </li>
        )
      })}
    </ul>
  )
}

function DismissedClusters() {
  const queryClient = useQueryClient()
  const [expanded, setExpanded] = useState(false)
  const dismissed = useQuery({
    queryKey: ['dismissed-audit-clusters'],
    queryFn: api.listDismissedClusters,
    enabled: expanded,
  })
  const restore = useMutation({
    mutationFn: (id: number) => api.restoreDismissedCluster(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['library-audit'] })
      queryClient.invalidateQueries({ queryKey: ['dismissed-audit-clusters'] })
    },
  })

  return (
    <div className="mt-6">
      <button
        className="text-xs text-neutral-400 underline underline-offset-2 hover:text-neutral-600 dark:hover:text-neutral-300"
        onClick={() => setExpanded((v) => !v)}
      >
        {expanded ? 'Hide dismissed' : 'Show dismissed clusters'}
      </button>
      {expanded && (
        <div className="mt-2">
          {dismissed.isLoading && <p className="text-xs text-neutral-400">Loading…</p>}
          {dismissed.data?.length === 0 && (
            <p className="text-xs text-neutral-400">Nothing dismissed yet.</p>
          )}
          <ul className="space-y-1">
            {dismissed.data?.map((d) => (
              <li key={d.id} className="flex items-center justify-between gap-4 text-xs text-neutral-500">
                <span>
                  {d.kind} cluster: member ids {d.member_ids.join(', ')}
                </span>
                <button
                  className="shrink-0 text-neutral-400 underline hover:text-neutral-700 dark:hover:text-neutral-300"
                  disabled={restore.isPending}
                  onClick={() => restore.mutate(d.id)}
                >
                  Restore
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

function TitleMergeRepairPanel() {
  const queryClient = useQueryClient()
  const repair = useMutation({
    mutationFn: api.repairTitleMerges,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['library-audit'] })
      queryClient.invalidateQueries({ queryKey: ['duplicates'] })
      queryClient.invalidateQueries({ queryKey: ['files'] })
    },
  })

  return (
    <div className="mt-8 rounded border border-neutral-200 p-3 dark:border-neutral-800">
      <h2 className="text-sm font-medium text-neutral-500">Repair falsely-merged books</h2>
      <p className="mt-1 max-w-2xl text-xs text-neutral-400">
        An earlier version matched titles too loosely, so two different books by the same author
        whose titles shared a prefix before a colon (e.g. "Mistborn: The Final Empire" and
        "Mistborn: The Well of Ascension") could end up sharing one record — and one of them
        flagged as a duplicate. This re-derives each file's own title and splits any that don't
        belong. Reads the database only; no Drive changes, no AI. Safe to run more than once.
      </p>
      <div className="mt-2 flex items-center gap-3">
        <button
          className="rounded border border-neutral-300 px-2 py-1 text-xs disabled:opacity-50 dark:border-neutral-700"
          disabled={repair.isPending}
          onClick={() => repair.mutate()}
        >
          {repair.isPending ? 'Repairing…' : 'Run repair'}
        </button>
        {repair.data && (
          <span className="text-xs text-neutral-500">
            {repair.data.books_split === 0
              ? 'Nothing to split — all records look right.'
              : `Split ${repair.data.files_moved} file${
                  repair.data.files_moved === 1 ? '' : 's'
                } off ${repair.data.books_split} record${
                  repair.data.books_split === 1 ? '' : 's'
                }. Run Organize to re-file them.`}
          </span>
        )}
        {repair.isError && <span className="text-xs text-red-600">Repair failed.</span>}
      </div>
    </div>
  )
}

export function LibraryAudit() {
  const audit = useQuery({ queryKey: ['library-audit'], queryFn: api.getLibraryAudit })
  const [tab, setTab] = useState<'split' | 'reident'>('split')

  return (
    <div className="p-6">
      <h1 className="text-xl font-semibold">Library Audit</h1>

      <div className="mt-3 flex gap-2 border-b border-neutral-200 dark:border-neutral-800">
        {(
          [
            ['split', 'Split records'],
            ['reident', 'Re-identification'],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={`-mb-px border-b-2 px-3 py-1.5 text-sm ${
              tab === key
                ? 'border-neutral-900 font-medium text-neutral-900 dark:border-neutral-100 dark:text-neutral-100'
                : 'border-transparent text-neutral-500 hover:text-neutral-800 dark:hover:text-neutral-200'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === 'reident' && (
        <div className="mt-4">
          <ReidentAuditPanel />
        </div>
      )}

      {tab === 'split' && (
        <div className="mt-4">
      <p className="max-w-2xl text-sm text-neutral-500">
        Flags series/author names that look similar enough to be the same thing split across two
        records — usually because identification phrased it differently across two batches. Since
        organize names Drive folders after these, a split record here almost always means a split
        folder too. These are suggestions to review, not certainties.
      </p>
      <p className="mt-1 max-w-2xl text-xs text-neutral-400">
        Series clusters can be investigated and fixed in place below. Author clusters don't have
        that yet — to fix one, move the files together in Drive into whichever folder should be
        canonical, then run Rebuild library from the Library page to re-derive records from what's
        actually there. Already reviewed one and decided it's fine as-is? Dismiss it so it stops
        showing up here — dismissing is per exact set of names, so if that set ever changes (e.g.
        one gets merged elsewhere) it can reappear as a different question.
      </p>

      {audit.isLoading && <p className="mt-6 text-sm text-neutral-500">Scanning…</p>}
      {audit.isError && <p className="mt-6 text-sm text-red-600">Failed to load the audit.</p>}

      {audit.data && (
        <>
          <div className="mt-6">
            <h2 className="text-sm font-medium text-neutral-500">Possibly-split series</h2>
            <ClusterList
              clusters={audit.data.similar_series}
              noun="series"
              kind="series"
              renderActions={(cluster) => <SeriesMergePanel cluster={cluster} />}
            />
          </div>

          <div className="mt-6">
            <h2 className="text-sm font-medium text-neutral-500">Possibly-split authors</h2>
            <ClusterList clusters={audit.data.similar_authors} noun="authors" kind="author" />
          </div>
        </>
      )}

          <TitleMergeRepairPanel />

          <DismissedClusters />
        </div>
      )}
    </div>
  )
}
