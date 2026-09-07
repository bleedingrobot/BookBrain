import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { CorrectFileForm } from '../components/CorrectFileForm'
import type { CorrectReviewRequest } from '../types/reviews'
import type { DescriptionBackfillEstimate, RebuildEstimate } from '../types/library'
import { api, ApiError } from '../services/api'
import { useCoverStatus } from '../hooks/useCoverStatus'
import { useDescriptionStatus } from '../hooks/useDescriptionStatus'
import { useEmbeddedMetadataStatus } from '../hooks/useEmbeddedMetadataStatus'
import { useOrganizeStatus } from '../hooks/useOrganizeStatus'
import { useRebuildStatus } from '../hooks/useRebuildStatus'

const STATUSES = ['inbox', 'review', 'unidentified', 'duplicate', 'organised', 'rejected'] as const

const STATUS_LABEL: Record<string, string> = {
  inbox: 'Inbox',
  review: 'Needs review',
  unidentified: 'Unidentified',
  duplicate: 'Duplicate',
  organised: 'Organised',
  rejected: 'Rejected',
}

const STATUS_BADGE: Record<string, string> = {
  inbox: 'bg-neutral-100 text-neutral-700 dark:bg-neutral-800 dark:text-neutral-300',
  review: 'bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300',
  unidentified: 'bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300',
  duplicate: 'bg-purple-100 text-purple-800 dark:bg-purple-900/40 dark:text-purple-300',
  organised: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300',
  rejected: 'bg-neutral-200 text-neutral-600 dark:bg-neutral-800 dark:text-neutral-400',
}

const REASON_LABEL: Record<string, string> = {
  multi_parent: 'in multiple folders',
  no_parent: 'no folder',
  manual_drift: 'moved outside the app',
  parse_failed: "couldn't be parsed",
  low_confidence: 'low confidence',
  previously_rejected: 'duplicate of a previously rejected file',
}

export function Library() {
  const queryClient = useQueryClient()
  const [status, setStatus] = useState<string | undefined>(undefined)
  const [showOrganised, setShowOrganised] = useState(false)
  const [showDuplicates, setShowDuplicates] = useState(false)
  const [organizeJobId, setOrganizeJobId] = useState<string | null>(null)
  const [organizeError, setOrganizeError] = useState<string | null>(null)
  const [confirmingClear, setConfirmingClear] = useState(false)
  const [clearError, setClearError] = useState<string | null>(null)
  const [rebuildJobId, setRebuildJobId] = useState<string | null>(null)
  const [rebuildError, setRebuildError] = useState<string | null>(null)
  const [coverJobId, setCoverJobId] = useState<string | null>(null)
  const [coverError, setCoverError] = useState<string | null>(null)
  const [coverStarting, setCoverStarting] = useState(false)
  const [indexBusy, setIndexBusy] = useState(false)
  const [indexError, setIndexError] = useState<string | null>(null)
  const [indexResult, setIndexResult] = useState<number | null>(null)
  const [descJobId, setDescJobId] = useState<string | null>(null)
  const [descError, setDescError] = useState<string | null>(null)
  const [descStarting, setDescStarting] = useState(false)
  const [descUseAi, setDescUseAi] = useState(false)
  const [mdJobId, setMdJobId] = useState<string | null>(null)
  const [mdError, setMdError] = useState<string | null>(null)
  const [mdStarting, setMdStarting] = useState(false)
  const [mdDryRun, setMdDryRun] = useState(false)
  const [organizeStarting, setOrganizeStarting] = useState(false)
  const [rebuildStarting, setRebuildStarting] = useState(false)
  const [rebuildConfirm, setRebuildConfirm] = useState<RebuildEstimate | 'unknown' | null>(null)
  const [descConfirm, setDescConfirm] = useState<DescriptionBackfillEstimate | 'unknown' | null>(null)
  const [exporting, setExporting] = useState(false)
  const [exportError, setExportError] = useState<string | null>(null)
  const [exportResult, setExportResult] = useState<{ name: string; url: string } | null>(null)
  const [removeError, setRemoveError] = useState<string | null>(null)
  const [confirmingRemoveId, setConfirmingRemoveId] = useState<number | null>(null)
  const [correctingId, setCorrectingId] = useState<number | null>(null)
  const [correctError, setCorrectError] = useState<string | null>(null)

  const files = useQuery({ queryKey: ['files', status], queryFn: () => api.listFiles(status) })
  const organizeSettings = useQuery({
    queryKey: ['organize-settings'],
    queryFn: api.getOrganizeSettings,
  })
  const organize = useOrganizeStatus(organizeJobId)
  const rebuild = useRebuildStatus(rebuildJobId)
  const covers = useCoverStatus(coverJobId)
  const descriptions = useDescriptionStatus(descJobId)
  const embeddedMetadata = useEmbeddedMetadataStatus(mdJobId)

  const clearLibrary = useMutation({
    mutationFn: api.clearLibrary,
    onSuccess: () => {
      setConfirmingClear(false)
      setClearError(null)
      queryClient.invalidateQueries({ queryKey: ['files'] })
    },
    onError: (err: unknown) =>
      setClearError(err instanceof ApiError ? err.message : 'Failed to clear library.'),
  })

  const removeFile = useMutation({
    mutationFn: api.removeFile,
    onSuccess: () => {
      setConfirmingRemoveId(null)
      setRemoveError(null)
      queryClient.invalidateQueries({ queryKey: ['files'] })
    },
    onError: (err: unknown) => {
      setConfirmingRemoveId(null)
      setRemoveError(err instanceof ApiError ? err.message : 'Failed to remove file.')
    },
  })

  const correctFile = useMutation({
    mutationFn: ({ id, body }: { id: number; body: CorrectReviewRequest }) =>
      api.correctFile(id, body),
    onSuccess: () => {
      setCorrectingId(null)
      setCorrectError(null)
      queryClient.invalidateQueries({ queryKey: ['files'] })
    },
    onError: (err: unknown) =>
      setCorrectError(err instanceof ApiError ? err.message : 'Failed to save correction.'),
  })

  useEffect(() => {
    if (organize.data?.status === 'done') {
      queryClient.invalidateQueries({ queryKey: ['files'] })
    }
  }, [organize.data?.status, queryClient])

  const startRebuild = async () => {
    setRebuildConfirm(null)
    setRebuildError(null)
    setRebuildStarting(true)
    try {
      const job = await api.rebuildLibrary()
      setRebuildJobId(job.job_id)
    } catch (err) {
      setRebuildError(err instanceof ApiError ? err.message : 'Failed to start rebuild.')
    } finally {
      setRebuildStarting(false)
    }
  }

  const requestRebuild = async () => {
    setRebuildError(null)
    setRebuildStarting(true)
    try {
      setRebuildConfirm(await api.rebuildEstimate())
    } catch {
      setRebuildConfirm('unknown')
    } finally {
      setRebuildStarting(false)
    }
  }

  const startDescriptions = async (useAi: boolean) => {
    setDescConfirm(null)
    setDescError(null)
    setDescStarting(true)
    try {
      const job = await api.backfillDescriptions(useAi)
      setDescJobId(job.job_id)
    } catch (err) {
      setDescError(err instanceof ApiError ? err.message : 'Failed to start description job.')
    } finally {
      setDescStarting(false)
    }
  }

  const requestDescriptions = async () => {
    if (!descUseAi) {
      await startDescriptions(false)
      return
    }
    setDescError(null)
    setDescStarting(true)
    try {
      setDescConfirm(await api.descriptionEstimate())
    } catch {
      setDescConfirm('unknown')
    } finally {
      setDescStarting(false)
    }
  }

  useEffect(() => {
    if (rebuild.data?.status === 'done') {
      queryClient.invalidateQueries({ queryKey: ['files'] })
    }
  }, [rebuild.data?.status, queryClient])

  // A tracked job can 404 out from under the UI (most commonly a dev-server
  // restart wiping in-memory job state) — without this, the button stays
  // disabled ("running…") forever because the job id never clears, even
  // though the job itself is gone.
  useEffect(() => {
    if (organize.isError) {
      setOrganizeError(
        'Lost track of this organize job — the server may have restarted mid-job. Check the file list before retrying, in case it partially finished.',
      )
      setOrganizeJobId(null)
    }
  }, [organize.isError])

  useEffect(() => {
    if (rebuild.isError) {
      setRebuildError('Lost track of this rebuild job — the server may have restarted mid-job. Try again.')
      setRebuildJobId(null)
    }
  }, [rebuild.isError])

  useEffect(() => {
    if (covers.isError) {
      setCoverError('Lost track of the cover job — the server may have restarted. Re-run to continue where it left off.')
      setCoverJobId(null)
    }
  }, [covers.isError])

  useEffect(() => {
    if (descriptions.isError) {
      setDescError('Lost track of the description job — the server may have restarted. Re-run to continue.')
      setDescJobId(null)
    }
  }, [descriptions.isError])

  useEffect(() => {
    if (descriptions.data?.status === 'done') {
      queryClient.invalidateQueries({ queryKey: ['files'] })
    }
  }, [descriptions.data?.status, queryClient])

  useEffect(() => {
    if (embeddedMetadata.isError) {
      setMdError('Lost track of the metadata job — the server may have restarted. Re-run to continue where it left off.')
      setMdJobId(null)
    }
  }, [embeddedMetadata.isError])

  const visibleFiles =
    status === undefined
      ? files.data?.filter(
          (f) =>
            (showOrganised || (f.status !== 'organised' && f.status !== 'rejected')) &&
            (showDuplicates || f.status !== 'duplicate'),
        )
      : files.data

  return (
    <div className="p-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold">Library</h1>
          <p className="mt-1 text-sm text-neutral-500">
            Every file the app knows about, whatever its status — nothing here is hidden.
          </p>
        </div>

        <div className="shrink-0 text-right">
          <div className="flex justify-end gap-2">
            <button
              className="rounded border border-neutral-300 px-3 py-1.5 text-sm disabled:opacity-50 dark:border-neutral-700"
              disabled={organizeStarting || organize.data?.status === 'running'}
              onClick={async () => {
                setOrganizeError(null)
                setOrganizeStarting(true)
                try {
                  const job = await api.startOrganize()
                  setOrganizeJobId(job.job_id)
                } catch (err) {
                  setOrganizeError(err instanceof ApiError ? err.message : 'Failed to start organize.')
                } finally {
                  setOrganizeStarting(false)
                }
              }}
            >
              {organizeSettings.data?.dry_run ? 'Organize (dry run)' : 'Organize'}
            </button>
            <button
              className="rounded border border-neutral-300 px-3 py-1.5 text-sm disabled:opacity-50 dark:border-neutral-700"
              title="Re-identifies every library file not already tracked (AI included). Shows an estimate first."
              disabled={rebuildStarting || rebuild.data?.status === 'running'}
              onClick={requestRebuild}
            >
              Rebuild library
            </button>
            <button
              className="rounded border border-neutral-300 px-3 py-1.5 text-sm disabled:opacity-50 dark:border-neutral-700"
              title="Rewrites bookbrain-index.json in the library folder — the metadata the viewer reads (author, series, description, ISBN, covers folder)."
              disabled={indexBusy}
              onClick={async () => {
                setIndexError(null)
                setIndexResult(null)
                setIndexBusy(true)
                try {
                  const r = await api.refreshLibraryIndex()
                  setIndexResult(r.books)
                } catch (err) {
                  setIndexError(err instanceof ApiError ? err.message : 'Failed to refresh index.')
                } finally {
                  setIndexBusy(false)
                }
              }}
            >
              {indexBusy ? 'Refreshing…' : 'Refresh viewer data'}
            </button>
            <button
              className="rounded border border-neutral-300 px-3 py-1.5 text-sm disabled:opacity-50 dark:border-neutral-700"
              title="Extracts a cover thumbnail from every organised EPUB into the Drive covers/ folder. Resumable — re-running only fills the gaps."
              disabled={coverStarting || covers.data?.status === 'running'}
              onClick={async () => {
                setCoverError(null)
                setCoverStarting(true)
                try {
                  const job = await api.generateCovers()
                  setCoverJobId(job.job_id)
                } catch (err) {
                  setCoverError(err instanceof ApiError ? err.message : 'Failed to start cover job.')
                } finally {
                  setCoverStarting(false)
                }
              }}
            >
              {covers.data?.status === 'running' ? 'Generating covers…' : 'Generate covers'}
            </button>
            <button
              className="rounded border border-neutral-300 px-3 py-1.5 text-sm disabled:opacity-50 dark:border-neutral-700"
              title="Fills in missing book descriptions from Google Books / Open Library (free). Tick the box to also have Claude write one for anything still blank (uses API credits — you'll see an estimate first)."
              disabled={descStarting || descriptions.data?.status === 'running'}
              onClick={requestDescriptions}
            >
              {descriptions.data?.status === 'running' ? 'Filling descriptions…' : 'Fill descriptions'}
            </button>
            <button
              className="rounded border border-neutral-300 px-3 py-1.5 text-sm disabled:opacity-50 dark:border-neutral-700"
              title="Writes the resolved title / author / series (and a cover, if the epub has none) into each organised .epub's own metadata, so Kobo and other readers show it correctly. Tick 'dry run' to preview what would change. Resumable."
              disabled={mdStarting || embeddedMetadata.data?.status === 'running'}
              onClick={async () => {
                setMdError(null)
                setMdStarting(true)
                try {
                  const job = await api.writeEmbeddedMetadata(mdDryRun)
                  setMdJobId(job.job_id)
                } catch (err) {
                  setMdError(err instanceof ApiError ? err.message : 'Failed to start metadata job.')
                } finally {
                  setMdStarting(false)
                }
              }}
            >
              {embeddedMetadata.data?.status === 'running'
                ? 'Writing metadata…'
                : mdDryRun
                  ? 'Fix embedded metadata (dry run)'
                  : 'Fix embedded metadata'}
            </button>
            <button
              className="rounded border border-neutral-300 px-3 py-1.5 text-sm disabled:opacity-50 dark:border-neutral-700"
              disabled={exporting}
              onClick={async () => {
                setExportError(null)
                setExportResult(null)
                setExporting(true)
                try {
                  const result = await api.exportLibrary()
                  setExportResult(result)
                  window.open(result.url, '_blank', 'noopener')
                } catch (err) {
                  setExportError(err instanceof ApiError ? err.message : 'Failed to export library.')
                } finally {
                  setExporting(false)
                }
              }}
            >
              {exporting ? 'Exporting…' : 'Export to Google Sheets'}
            </button>
            <button
              className="rounded border border-red-300 px-3 py-1.5 text-sm text-red-700 disabled:opacity-50 dark:border-red-800 dark:text-red-400"
              onClick={() => setConfirmingClear(true)}
            >
              Clear library
            </button>
          </div>
          {exportError && <p className="mt-1 text-xs text-red-600">{exportError}</p>}
          {exportResult && !exportError && (
            <p className="mt-1 text-xs text-neutral-500">
              Exported to{' '}
              <a
                className="underline"
                href={exportResult.url}
                target="_blank"
                rel="noopener noreferrer"
              >
                {exportResult.name}
              </a>
            </p>
          )}
          {organizeError && <p className="mt-1 text-xs text-red-600">{organizeError}</p>}
          {organize.data && (
            <p className="mt-1 text-xs text-neutral-500">
              organize: {organize.data.status}
              {organize.data.detail ? ` — ${organize.data.detail}` : ''}
            </p>
          )}
          {organize.data && organize.data.failures.length > 0 && (
            <ul className="mt-1 max-h-40 max-w-sm divide-y divide-neutral-100 overflow-y-auto rounded border border-red-200 text-left text-xs dark:divide-neutral-800 dark:border-red-900">
              {organize.data.failures.map((f, i) => (
                <li key={i} className="px-2 py-1.5">
                  <div className="truncate text-neutral-700 dark:text-neutral-300">{f.filename}</div>
                  <div className="truncate text-red-600 dark:text-red-400">{f.reason}</div>
                </li>
              ))}
            </ul>
          )}
          {rebuildError && <p className="mt-1 text-xs text-red-600">{rebuildError}</p>}
          {rebuild.data && (
            <p className="mt-1 text-xs text-neutral-500">
              rebuild: {rebuild.data.status}
              {rebuild.data.detail ? ` — ${rebuild.data.detail}` : ''}
            </p>
          )}
          {indexError && <p className="mt-1 text-xs text-red-600">{indexError}</p>}
          {indexResult !== null && !indexError && (
            <p className="mt-1 text-xs text-neutral-500">viewer data refreshed — {indexResult} books</p>
          )}
          {coverError && <p className="mt-1 text-xs text-red-600">{coverError}</p>}
          {covers.data && (
            <p className="mt-1 text-xs text-neutral-500">
              covers: {covers.data.status} — {covers.data.generated} made,{' '}
              {covers.data.no_cover} with no cover, {covers.data.failed} failed
              {covers.data.status === 'running' && covers.data.remaining > 0
                ? `, ${covers.data.remaining} to go`
                : ''}
            </p>
          )}
          <label className="mt-1 flex items-center justify-end gap-1.5 text-xs text-neutral-500">
            <input
              type="checkbox"
              checked={descUseAi}
              onChange={(e) => setDescUseAi(e.target.checked)}
            />
            Also write blurbs with Claude for anything still blank (uses API credits)
          </label>
          <label className="mt-1 flex items-center justify-end gap-1.5 text-xs text-neutral-500">
            <input
              type="checkbox"
              checked={mdDryRun}
              onChange={(e) => setMdDryRun(e.target.checked)}
            />
            Dry run — preview the embedded-metadata changes without touching any file
          </label>
          {mdError && <p className="mt-1 text-xs text-red-600">{mdError}</p>}
          {embeddedMetadata.data && (
            <p className="mt-1 text-xs text-neutral-500">
              embedded metadata{embeddedMetadata.data.dry_run ? ' (dry run)' : ''}:{' '}
              {embeddedMetadata.data.status} — {embeddedMetadata.data.updated}{' '}
              {embeddedMetadata.data.dry_run ? 'would change' : 'written'},{' '}
              {embeddedMetadata.data.skipped} already correct, {embeddedMetadata.data.failed} failed
              {embeddedMetadata.data.status === 'running' && embeddedMetadata.data.remaining > 0
                ? `, ${embeddedMetadata.data.remaining} to go`
                : ''}
            </p>
          )}
          {descError && <p className="mt-1 text-xs text-red-600">{descError}</p>}
          {descriptions.data && (
            <p className="mt-1 text-xs text-neutral-500">
              descriptions: {descriptions.data.status} — {descriptions.data.from_provider} from a
              source
              {descriptions.data.from_ai > 0 ? `, ${descriptions.data.from_ai} from Claude` : ''},{' '}
              {descriptions.data.not_found} not found
              {descriptions.data.status === 'running' && descriptions.data.remaining > 0
                ? `, ${descriptions.data.remaining} to go`
                : ''}
            </p>
          )}

          {rebuildConfirm && (
            <div className="mt-3 max-w-sm space-y-2 rounded border border-amber-300 p-3 text-left text-xs dark:border-amber-800">
              <p className="text-neutral-700 dark:text-neutral-300">
                {rebuildConfirm === 'unknown'
                  ? "Couldn't estimate the size of this rebuild (Drive listing failed). It re-identifies every library file not already tracked, AI included."
                  : `This re-identifies about ${rebuildConfirm.files_to_identify} file${
                      rebuildConfirm.files_to_identify === 1 ? '' : 's'
                    } — roughly $${rebuildConfirm.estimated_cost_usd.toFixed(2)} in API credits. Runs unattended once started.`}
              </p>
              <div className="flex gap-2">
                <button
                  className="rounded bg-amber-600 px-3 py-1.5 text-white disabled:opacity-50"
                  disabled={rebuildStarting}
                  onClick={startRebuild}
                >
                  Yes, rebuild
                </button>
                <button
                  className="rounded border border-neutral-300 px-3 py-1.5 dark:border-neutral-700"
                  onClick={() => setRebuildConfirm(null)}
                >
                  Cancel
                </button>
              </div>
            </div>
          )}

          {descConfirm && (
            <div className="mt-3 max-w-sm space-y-2 rounded border border-amber-300 p-3 text-left text-xs dark:border-amber-800">
              <p className="text-neutral-700 dark:text-neutral-300">
                {descConfirm === 'unknown'
                  ? "Couldn't estimate this run, but it will write model blurbs for books still missing one (uses API credits)."
                  : `About ${descConfirm.books_missing} book${
                      descConfirm.books_missing === 1 ? '' : 's'
                    } still lack a description. This run writes up to ${descConfirm.will_process} model blurbs — roughly $${descConfirm.estimated_cost_usd.toFixed(
                      2,
                    )}. Re-run to continue in batches of ${descConfirm.cap}.`}
              </p>
              <div className="flex gap-2">
                <button
                  className="rounded bg-amber-600 px-3 py-1.5 text-white disabled:opacity-50"
                  disabled={descStarting}
                  onClick={() => startDescriptions(true)}
                >
                  Yes, run it
                </button>
                <button
                  className="rounded border border-neutral-300 px-3 py-1.5 dark:border-neutral-700"
                  onClick={() => setDescConfirm(null)}
                >
                  Cancel
                </button>
              </div>
            </div>
          )}

          {confirmingClear && (
            <div className="mt-3 max-w-sm space-y-2 rounded border border-red-300 p-3 text-left text-xs dark:border-red-800">
              <p className="text-red-700 dark:text-red-400">
                This deletes every tracked file, book, review, and operation from the app's
                database so you can rescan from scratch — sha256 duplicate detection won't
                block re-processing anymore. It does not touch anything in Google Drive, and
                your connection/folder settings are kept.
              </p>
              {clearError && <p className="text-red-600">{clearError}</p>}
              <div className="flex gap-2">
                <button
                  className="rounded bg-red-600 px-3 py-1.5 text-white disabled:opacity-50"
                  disabled={clearLibrary.isPending}
                  onClick={() => clearLibrary.mutate()}
                >
                  Yes, clear everything
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
        </div>
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <button
          onClick={() => setStatus(undefined)}
          className={`rounded px-3 py-1 text-xs font-medium ${
            status === undefined
              ? 'bg-neutral-900 text-white dark:bg-neutral-100 dark:text-neutral-900'
              : 'bg-neutral-100 text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300'
          }`}
        >
          All
        </button>
        {STATUSES.map((s) => (
          <button
            key={s}
            onClick={() => setStatus(s)}
            className={`rounded px-3 py-1 text-xs font-medium ${
              status === s
                ? 'bg-neutral-900 text-white dark:bg-neutral-100 dark:text-neutral-900'
                : 'bg-neutral-100 text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300'
            }`}
          >
            {STATUS_LABEL[s]}
          </button>
        ))}

        {status === undefined && (
          <>
            <label className="ml-2 flex items-center gap-1.5 text-xs text-neutral-500">
              <input
                type="checkbox"
                checked={showOrganised}
                onChange={(e) => setShowOrganised(e.target.checked)}
              />
              Show organised/rejected
            </label>
            <label className="flex items-center gap-1.5 text-xs text-neutral-500">
              <input
                type="checkbox"
                checked={showDuplicates}
                onChange={(e) => setShowDuplicates(e.target.checked)}
              />
              Show duplicates
            </label>
          </>
        )}
      </div>

      {files.isLoading && <div className="mt-6 text-sm text-neutral-500">Loading...</div>}
      {files.isError && <div className="mt-6 text-sm text-neutral-500">Failed to load files.</div>}
      {removeError && <div className="mt-4 text-sm text-red-600">{removeError}</div>}

      <ul className="mt-4 divide-y divide-neutral-100 text-sm dark:divide-neutral-800">
        {visibleFiles?.map((file) => (
          <li key={file.id} className="py-3">
            <div className="flex items-center justify-between gap-3">
              <div className="min-w-0">
                <div className="truncate font-medium">
                  {file.book_title ?? file.filename}
                  {file.book_author && (
                    <span className="ml-2 font-normal text-neutral-500">by {file.book_author}</span>
                  )}
                </div>
                <div className="truncate text-xs text-neutral-400">
                  {file.book_series && (
                    <span>
                      {file.book_series}
                      {file.book_series_number !== null && ` #${file.book_series_number}`}
                      {' · '}
                    </span>
                  )}
                  {file.book_title && file.filename}
                </div>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                {file.computed_confidence !== null && (
                  <span className="text-xs text-neutral-400">confidence {file.computed_confidence}</span>
                )}
                <span className={`rounded px-2 py-0.5 text-xs ${STATUS_BADGE[file.status] ?? ''}`}>
                  {STATUS_LABEL[file.status] ?? file.status}
                </span>
                {file.book_title && file.status !== 'rejected' && (
                  <button
                    className="rounded border border-neutral-300 px-2 py-0.5 text-xs text-neutral-600 dark:border-neutral-700 dark:text-neutral-300"
                    title="Fix the title / author / series, then run Organize to re-file it."
                    onClick={() => {
                      setCorrectError(null)
                      setCorrectingId(correctingId === file.id ? null : file.id)
                    }}
                  >
                    Correct
                  </button>
                )}
                {file.status === 'unidentified' &&
                  (confirmingRemoveId === file.id ? (
                    <span className="flex items-center gap-1 text-xs">
                      <button
                        className="rounded bg-red-600 px-2 py-0.5 text-white disabled:opacity-50"
                        disabled={removeFile.isPending}
                        onClick={() => removeFile.mutate(file.id)}
                      >
                        {removeFile.isPending ? '…' : 'Confirm'}
                      </button>
                      <button
                        className="rounded border border-neutral-300 px-2 py-0.5 dark:border-neutral-700"
                        disabled={removeFile.isPending}
                        onClick={() => setConfirmingRemoveId(null)}
                      >
                        Cancel
                      </button>
                    </span>
                  ) : (
                    <button
                      className="rounded border border-neutral-300 px-2 py-0.5 text-xs text-neutral-600 dark:border-neutral-700 dark:text-neutral-300"
                      title="Moves the file to Google Drive's Trash — recoverable there, not a permanent delete."
                      onClick={() => {
                        setRemoveError(null)
                        setConfirmingRemoveId(file.id)
                      }}
                    >
                      Remove
                    </button>
                  ))}
              </div>
            </div>
            {(file.status_reason || file.ai_reasoning) && (
              <p className="mt-1 text-xs text-neutral-500">
                {file.status_reason && (REASON_LABEL[file.status_reason] ?? file.status_reason)}
                {file.status_reason && file.ai_reasoning && ' — '}
                {file.ai_reasoning}
              </p>
            )}
            {correctingId === file.id && (
              <CorrectFileForm
                file={file}
                busy={correctFile.isPending}
                error={correctError}
                onSubmit={(body) => correctFile.mutate({ id: file.id, body })}
                onCancel={() => setCorrectingId(null)}
              />
            )}
          </li>
        ))}
        {visibleFiles?.length === 0 && (
          <li className="py-4 text-neutral-400">No files match this filter.</li>
        )}
      </ul>
    </div>
  )
}
