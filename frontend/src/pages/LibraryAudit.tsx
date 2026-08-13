import { useQuery } from '@tanstack/react-query'
import type { SimilarNameCluster } from '../types/libraryAudit'
import { api } from '../services/api'

function ClusterList({ clusters, noun }: { clusters: SimilarNameCluster[]; noun: string }) {
  if (clusters.length === 0) {
    return <p className="mt-2 text-sm text-neutral-400">No likely-duplicate {noun} found.</p>
  }

  return (
    <ul className="mt-2 space-y-3">
      {clusters.map((cluster, i) => (
        <li key={i} className="rounded border border-amber-300 p-3 text-sm dark:border-amber-800">
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
        </li>
      ))}
    </ul>
  )
}

export function LibraryAudit() {
  const audit = useQuery({ queryKey: ['library-audit'], queryFn: api.getLibraryAudit })

  return (
    <div className="p-6">
      <h1 className="text-xl font-semibold">Library Audit</h1>
      <p className="mt-1 max-w-2xl text-sm text-neutral-500">
        Flags series/author names that look similar enough to be the same thing split across two
        records — usually because identification phrased it differently across two batches. Since
        organize names Drive folders after these, a split record here almost always means a split
        folder too. These are suggestions to review, not certainties.
      </p>
      <p className="mt-1 max-w-2xl text-xs text-neutral-400">
        There's no merge action yet — to fix one, move the files together in Drive into whichever
        folder should be canonical, then run Rebuild library from the Library page to re-derive
        records from what's actually there.
      </p>

      {audit.isLoading && <p className="mt-6 text-sm text-neutral-500">Scanning…</p>}
      {audit.isError && <p className="mt-6 text-sm text-red-600">Failed to load the audit.</p>}

      {audit.data && (
        <>
          <div className="mt-6">
            <h2 className="text-sm font-medium text-neutral-500">Possibly-split series</h2>
            <ClusterList clusters={audit.data.similar_series} noun="series" />
          </div>

          <div className="mt-6">
            <h2 className="text-sm font-medium text-neutral-500">Possibly-split authors</h2>
            <ClusterList clusters={audit.data.similar_authors} noun="authors" />
          </div>
        </>
      )}
    </div>
  )
}
