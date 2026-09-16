import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, ApiError } from '../services/api'
import type { DiscoverySidecar } from '../types/library'

// key in the discovery-status payload → label + the fire-and-forget refresh call
const ROWS: { key: string; label: string; refresh: () => Promise<unknown> }[] = [
  { key: 'index', label: 'Library index', refresh: api.refreshLibraryIndex },
  { key: 'reading', label: 'Reading status (Hardcover)', refresh: api.refreshReadingFile },
  { key: 'recommendations', label: 'Recommendations', refresh: api.refreshRecommendationsFile },
  { key: 'newReleases', label: 'New & upcoming releases', refresh: api.refreshNewReleasesFile },
  { key: 'prompts', label: 'Library answers (prompts)', refresh: api.refreshPromptsFile },
  { key: 'lists', label: 'Curated lists', refresh: api.refreshListsFile },
  { key: 'news', label: 'SFF news', refresh: api.refreshNewsFile },
  { key: 'embeddings', label: 'Semantic search index', refresh: api.refreshEmbeddingsFile },
]

const STALE_MS = 36 * 60 * 60 * 1000

function ago(iso: string | null): { text: string; stale: boolean } {
  if (!iso) return { text: 'never', stale: true }
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return { text: '—', stale: false }
  const diff = Date.now() - then
  const stale = diff > STALE_MS
  const h = Math.round(diff / 3_600_000)
  if (h < 1) return { text: 'just now', stale }
  if (h < 48) return { text: `${h}h ago`, stale }
  return { text: `${Math.round(h / 24)}d ago`, stale }
}

function Row({ label, refreshKey, status }: { label: string; refreshKey: string; status: DiscoverySidecar | null }) {
  const queryClient = useQueryClient()
  const row = ROWS.find((r) => r.key === refreshKey)!
  const refresh = useMutation({
    mutationFn: row.refresh,
    onSettled: () => queryClient.invalidateQueries({ queryKey: ['discovery-status'] }),
  })

  const when = ago(status?.generatedAt ?? null)

  return (
    <div className="flex items-center justify-between gap-3 py-1.5">
      <div className="min-w-0">
        <span>{label}</span>
        {status?.partial && (
          <span className="ml-2 text-xs text-amber-600 dark:text-amber-500">partial</span>
        )}
        {typeof status?.count === 'number' && (
          <span className="ml-2 text-xs text-neutral-400">{status.count}</span>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-3">
        <span className={`text-xs ${when.stale ? 'text-red-600 dark:text-red-400' : 'text-neutral-400'}`}>
          {when.text}
        </span>
        <button
          className="rounded border border-neutral-300 px-2 py-0.5 text-xs disabled:opacity-50 dark:border-neutral-700"
          disabled={refresh.isPending}
          onClick={() => refresh.mutate()}
        >
          {refresh.isPending ? '…' : 'Refresh'}
        </button>
      </div>
    </div>
  )
}

export function DiscoveryPanel() {
  const queryClient = useQueryClient()
  const status = useQuery({ queryKey: ['discovery-status'], queryFn: api.getDiscoveryStatus })

  const resync = useMutation({
    mutationFn: async () => {
      // The slow ones — page through the library / hit Hardcover per author.
      // Fire them and don't wait past the first; the nightly + a repeat click
      // converge. A fetch timeout here means "still running", not "failed".
      await Promise.allSettled([
        api.resyncSeriesCatalog(),
        api.resyncBookRecs(),
        api.resyncNewReleases(),
        api.computeEmbeddings(),
      ])
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: ['discovery-status'] }),
  })

  if (status.isError) {
    const msg =
      status.error instanceof ApiError && status.error.status === 400
        ? 'No library folder configured yet.'
        : status.error instanceof ApiError && status.error.status === 401
          ? 'Not connected to Google Drive.'
          : 'Could not load discovery status.'
    return (
      <section className="mt-8">
        <h2 className="font-medium">Discovery data</h2>
        <p className="mt-2 text-sm text-neutral-500">{msg}</p>
      </section>
    )
  }

  return (
    <section className="mt-8">
      <h2 className="font-medium">Discovery data</h2>
      <p className="mt-1 text-xs text-neutral-500">
        The sidecars the library-viewer reads. Refreshed nightly; use these to force one now if it
        looks stale (red) or wrong.
      </p>

      <div className="mt-3 divide-y divide-neutral-100 text-sm dark:divide-neutral-800">
        {status.isLoading ? (
          <p className="py-2 text-xs text-neutral-400">Loading…</p>
        ) : (
          ROWS.map((r) => (
            <Row key={r.key} label={r.label} refreshKey={r.key} status={status.data?.[r.key] ?? null} />
          ))
        )}
      </div>

      <div className="mt-3 flex items-center gap-3">
        <button
          className="rounded border border-neutral-300 px-2.5 py-1 text-xs disabled:opacity-50 dark:border-neutral-700"
          disabled={resync.isPending}
          onClick={() => resync.mutate()}
        >
          {resync.isPending ? 'Re-syncing…' : 'Full Hardcover re-sync (slow)'}
        </button>
        <span className="text-xs text-neutral-400">
          Re-pulls series, recs, per-author releases &amp; embeddings from scratch — minutes. Refresh
          the individual rows above after it finishes.
        </span>
      </div>
      {resync.isError && (
        <p className="mt-1 text-xs text-neutral-400">
          Still running on the server — check the rows above in a minute.
        </p>
      )}
    </section>
  )
}
