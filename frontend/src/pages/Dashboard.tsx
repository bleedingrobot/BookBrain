import { useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { ProgressBar } from '../components/ProgressBar'
import { api, ApiError } from '../services/api'
import { useOrganizeStatus } from '../hooks/useOrganizeStatus'
import { useScanStatus } from '../hooks/useScanStatus'
import { Duplicates } from './Duplicates'
import { ReviewQueue } from './ReviewQueue'

function formatBytes(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

// Tracks "how many were there when this batch started" against the live
// (shrinking) count, so progress reads as "5 of 8 done" instead of just
// "3 left". Sticks at its high point while the count is nonzero (so it
// still reads "8 of 8" once finished, rather than vanishing at 0) and only
// resets once a genuinely new batch starts from empty.
function useProgressBaseline(count: number): number {
  const [baseline, setBaseline] = useState(0)
  const prevCount = useRef(0)

  useEffect(() => {
    setBaseline((prev) => {
      if (count === 0) return prev
      if (prevCount.current === 0) return count
      return Math.max(prev, count)
    })
    prevCount.current = count
  }, [count])

  return baseline
}

export function Dashboard() {
  const queryClient = useQueryClient()

  const [jobId, setJobId] = useState<string | null>(null)
  const [scanError, setScanError] = useState<string | null>(null)
  const [scanStarting, setScanStarting] = useState(false)

  const [organizeJobId, setOrganizeJobId] = useState<string | null>(null)
  const [organizeError, setOrganizeError] = useState<string | null>(null)
  const [organizeStarting, setOrganizeStarting] = useState(false)

  const [showReview, setShowReview] = useState(false)
  const [showDuplicates, setShowDuplicates] = useState(false)
  const [showTorrents, setShowTorrents] = useState(false)
  const [torrentsChecking, setTorrentsChecking] = useState(false)
  const [torrentsError, setTorrentsError] = useState<string | null>(null)
  const [selectedTorrents, setSelectedTorrents] = useState<Set<number>>(new Set())
  const [torrentsBusy, setTorrentsBusy] = useState(false)

  const health = useQuery({ queryKey: ['health'], queryFn: api.health })
  const nightly = useQuery({ queryKey: ['nightly-settings'], queryFn: api.getNightlySettings })
  const authStatus = useQuery({ queryKey: ['auth-status'], queryFn: api.authStatus })
  const inboxFolder = useQuery({
    queryKey: ['inbox-folder'],
    queryFn: api.driveInboxFolder,
    enabled: authStatus.data?.connected === true,
  })
  const organizeSettings = useQuery({
    queryKey: ['organize-settings'],
    queryFn: api.getOrganizeSettings,
  })
  const scan = useScanStatus(jobId)
  const organize = useOrganizeStatus(organizeJobId)

  const pendingReviews = useQuery({ queryKey: ['reviews', 'pending'], queryFn: () => api.listReviews('pending') })
  const duplicates = useQuery({ queryKey: ['duplicates'], queryFn: api.listDuplicates })
  // Organize commits each file individually on the backend, so polling this
  // while a job is running shows the count shrink book-by-book instead of
  // jumping straight from full to empty once the whole job finishes.
  const readyToOrganize = useQuery({
    queryKey: ['files', 'inbox'],
    queryFn: () => api.listFiles('inbox'),
    refetchInterval: () => (organize.data?.status === 'running' ? 1000 : false),
  })
  const localPending = useQuery({ queryKey: ['local-scan', 'pending'], queryFn: api.getPendingLocalFiles })

  const readyToScan = authStatus.data?.connected === true && inboxFolder.data != null

  // Polls the live Drive folder (not the DB) so newly-dropped files show up
  // here even before the next scan picks them up. Paused while a scan is
  // actively running — the scan is already reading/clearing this same
  // folder as fast as it can, so polling adds load without adding anything
  // useful to look at; the scan-done effect below refreshes it the moment
  // that stops being true.
  const bookDump = useQuery({
    queryKey: ['drive-files'],
    queryFn: api.driveFiles,
    enabled: readyToScan,
    refetchInterval: () => (scan.data?.status === 'running' ? false : 8000),
  })
  const reviewCount = pendingReviews.data?.length ?? 0
  const duplicateCount = duplicates.data?.length ?? 0
  const organizeCount = readyToOrganize.data?.length ?? 0
  const torrentsCount = localPending.data?.length ?? 0

  const reviewBaseline = useProgressBaseline(reviewCount)
  const duplicateBaseline = useProgressBaseline(duplicateCount)
  const organizeBaseline = useProgressBaseline(organizeCount)
  const torrentsBaseline = useProgressBaseline(torrentsCount)

  // A scan can create new reviews/duplicates/organize-ready files — recheck
  // all three the moment it finishes instead of waiting on a manual
  // "Refresh checklist" click or an unrelated background refetch.
  useEffect(() => {
    if (scan.data?.status === 'done') {
      queryClient.invalidateQueries({ queryKey: ['reviews', 'pending'] })
      queryClient.invalidateQueries({ queryKey: ['duplicates'] })
      queryClient.invalidateQueries({ queryKey: ['files'] })
      queryClient.invalidateQueries({ queryKey: ['drive-files'] })
    }
  }, [scan.data?.status, queryClient])

  // Guarantees the "N ready to organize" card and progress bar are fully in
  // sync with the finished job the instant it completes, rather than
  // waiting on the next 1s poll tick.
  useEffect(() => {
    if (organize.data?.status === 'done') {
      queryClient.invalidateQueries({ queryKey: ['files'] })
    }
  }, [organize.data?.status, queryClient])

  // A tracked job can 404 out from under the UI (most commonly a dev-server
  // restart wiping in-memory job state) — without this, the "Start
  // scan"/"Organize" button stays disabled ("running…") forever because
  // jobId never clears, even though the job itself is gone.
  useEffect(() => {
    if (scan.isError) {
      setScanError('Lost track of this scan — the server may have restarted mid-job. Try again.')
      setJobId(null)
    }
  }, [scan.isError])

  useEffect(() => {
    if (organize.isError) {
      setOrganizeError(
        'Lost track of this organize job — the server may have restarted mid-job. Check the file list below before retrying, in case it partially finished.',
      )
      setOrganizeJobId(null)
    }
  }, [organize.isError])

  async function handleStartScan() {
    setScanError(null)
    setScanStarting(true)
    try {
      const job = await api.startScan()
      setJobId(job.job_id)
    } catch (err) {
      setScanError(err instanceof ApiError ? err.message : 'Failed to start scan.')
    } finally {
      setScanStarting(false)
    }
  }

  async function handleOrganize() {
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
  }

  async function handleCheckTorrents() {
    setTorrentsError(null)
    setTorrentsChecking(true)
    try {
      await api.scanLocalFolder()
      queryClient.invalidateQueries({ queryKey: ['local-scan', 'pending'] })
    } catch (err) {
      setTorrentsError(err instanceof ApiError ? err.message : 'Failed to check the Torrents folder.')
    } finally {
      setTorrentsChecking(false)
    }
  }

  function toggleTorrentSelected(id: number) {
    setSelectedTorrents((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  async function handleCopySelectedTorrents() {
    if (selectedTorrents.size === 0) return
    setTorrentsBusy(true)
    setTorrentsError(null)
    try {
      await api.copyLocalFiles(Array.from(selectedTorrents))
      setSelectedTorrents(new Set())
      queryClient.invalidateQueries({ queryKey: ['local-scan', 'pending'] })
    } catch (err) {
      setTorrentsError(err instanceof ApiError ? err.message : 'Failed to copy to Book Dump.')
    } finally {
      setTorrentsBusy(false)
    }
  }

  async function handleDismissSelectedTorrents() {
    if (selectedTorrents.size === 0) return
    setTorrentsBusy(true)
    setTorrentsError(null)
    try {
      await api.dismissLocalFiles(Array.from(selectedTorrents))
      setSelectedTorrents(new Set())
      queryClient.invalidateQueries({ queryKey: ['local-scan', 'pending'] })
    } catch (err) {
      setTorrentsError(err instanceof ApiError ? err.message : 'Failed to dismiss.')
    } finally {
      setTorrentsBusy(false)
    }
  }

  return (
    <div className="p-6">
      <h1 className="text-xl font-semibold">Dashboard</h1>

      <p className="mt-4 text-sm">
        Backend health:{' '}
        {health.isLoading ? 'checking...' : health.isError ? 'unreachable' : health.data?.status}
      </p>

      {nightly.data && (
        <p className="mt-1 text-sm text-neutral-500">
          Nightly run:{' '}
          {nightly.data.enabled
            ? `on, ${String(nightly.data.hour).padStart(2, '0')}:00`
            : 'off'}
          {nightly.data.last_run ? (
            <>
              {' — last '}
              {new Date(
                nightly.data.last_run.finished_at ?? nightly.data.last_run.started_at,
              ).toLocaleString()}
              {': '}
              {nightly.data.last_run.status === 'failed' ? (
                <span className="text-red-600 dark:text-red-400">
                  failed — {nightly.data.last_run.error}
                </span>
              ) : nightly.data.last_run.status === 'running' ? (
                'running…'
              ) : (
                nightly.data.last_run.summary
              )}
            </>
          ) : (
            ' — never run'
          )}{' '}
          <Link to="/settings" className="underline">
            change
          </Link>
        </p>
      )}

      <div className="mt-4 rounded border border-neutral-200 p-4 dark:border-neutral-800">
        <h2 className="text-sm font-medium text-neutral-500">Progress</h2>
        <ul className="mt-3 space-y-2.5">
          <li className="flex items-center justify-between gap-4 text-sm">
            <span>Scan</span>
            <span className="flex items-center gap-2 text-xs text-neutral-400">
              {scan.data?.status === 'running' && (
                <span className="h-3 w-3 animate-spin rounded-full border-2 border-neutral-300 border-t-neutral-600 dark:border-neutral-700 dark:border-t-neutral-300" />
              )}
              {!scan.data ? 'not started' : scan.data.status === 'running' ? 'running…' : 'done'}
            </span>
          </li>

          {torrentsBaseline > 0 && (
            <li className="flex items-center justify-between gap-4 text-sm">
              <span>Torrents folder</span>
              <ProgressBar completed={torrentsBaseline - torrentsCount} total={torrentsBaseline} />
            </li>
          )}

          {reviewBaseline > 0 && (
            <li className="flex items-center justify-between gap-4 text-sm">
              <span>Review</span>
              <ProgressBar completed={reviewBaseline - reviewCount} total={reviewBaseline} />
            </li>
          )}

          {duplicateBaseline > 0 && (
            <li className="flex items-center justify-between gap-4 text-sm">
              <span>Duplicates</span>
              <ProgressBar completed={duplicateBaseline - duplicateCount} total={duplicateBaseline} />
            </li>
          )}

          {organizeBaseline > 0 && (
            <li className="flex items-center justify-between gap-4 text-sm">
              <span>Organize</span>
              <ProgressBar completed={organizeBaseline - organizeCount} total={organizeBaseline} />
            </li>
          )}

          {torrentsBaseline === 0 &&
            reviewBaseline === 0 &&
            duplicateBaseline === 0 &&
            organizeBaseline === 0 &&
            !scan.data && (
              <li className="text-xs text-neutral-400">Nothing tracked yet — run a scan to get started.</li>
            )}
        </ul>
      </div>

      {readyToScan && (
        <div className="mt-4 rounded border border-neutral-200 p-3 dark:border-neutral-800">
          <div className="flex items-center justify-between gap-2">
            <h2 className="text-xs font-medium text-neutral-500">
              Book Dump{bookDump.data ? ` (${bookDump.data.length})` : ''}
            </h2>
            <span className="flex items-center gap-2 text-xs text-neutral-400">
              {bookDump.isFetching && <span>syncing…</span>}
              <button className="underline disabled:opacity-50" disabled={bookDump.isFetching} onClick={() => bookDump.refetch()}>
                Refresh
              </button>
            </span>
          </div>

          {bookDump.data && bookDump.data.length > 0 ? (
            <ul className="mt-2 max-h-40 divide-y divide-neutral-100 overflow-y-auto text-xs dark:divide-neutral-800">
              {bookDump.data.map((f) => (
                <li key={f.id} className="flex items-center gap-3 py-1">
                  <span className="min-w-0 flex-1 truncate">{f.name}</span>
                  <span className="shrink-0 text-neutral-400">{formatBytes(f.size_bytes)}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-2 text-xs text-neutral-400">
              {bookDump.isLoading ? 'loading…' : 'Empty'}
            </p>
          )}
        </div>
      )}

      {!readyToScan && !authStatus.isLoading && (
        <p className="mt-2 text-sm text-neutral-500">
          {authStatus.data?.connected
            ? 'Google Drive connected, but no inbox folder is set up yet.'
            : 'Not connected to Google Drive yet.'}{' '}
          <Link to="/settings" className="underline">
            Go to Settings
          </Link>
          .
        </p>
      )}

      {/* Step 0: Torrents folder — local files need to land in Book Dump
          before a Drive scan would ever find them, so this comes first. */}
      <div className="mt-6 rounded border border-neutral-200 p-4 dark:border-neutral-800">
        <div className="flex items-center justify-between gap-4">
          <p className="text-sm text-neutral-500">Check the Torrents folder for new ebooks.</p>
          <button
            className="shrink-0 rounded border border-neutral-300 px-3 py-1.5 text-xs disabled:opacity-50 dark:border-neutral-700"
            disabled={torrentsChecking}
            onClick={handleCheckTorrents}
          >
            {torrentsChecking ? 'Checking…' : 'Check Torrents folder'}
          </button>
        </div>

        {torrentsError && <p className="mt-2 text-sm text-red-600">{torrentsError}</p>}

        {torrentsCount > 0 && (
          <div className="mt-3 border-t border-neutral-200 pt-3 dark:border-neutral-800">
            {!showTorrents ? (
              <div className="flex items-center justify-between gap-4">
                <p className="text-sm">
                  {torrentsCount} new file{torrentsCount === 1 ? '' : 's'} found. Would you like to copy{' '}
                  {torrentsCount === 1 ? 'it' : 'them'} to Book Dump?
                </p>
                <button
                  className="shrink-0 rounded bg-neutral-900 px-3 py-1.5 text-xs text-white dark:bg-neutral-100 dark:text-neutral-900"
                  onClick={() => setShowTorrents(true)}
                >
                  Review now
                </button>
              </div>
            ) : (
              <div>
                <div className="mb-3 flex items-center justify-between">
                  <h2 className="text-sm font-medium text-neutral-500">New files</h2>
                  <button className="text-xs text-neutral-400 underline" onClick={() => setShowTorrents(false)}>
                    Hide
                  </button>
                </div>

                <div className="mb-2 flex flex-wrap items-center gap-3">
                  <button
                    className="text-xs text-neutral-400 underline"
                    onClick={() =>
                      setSelectedTorrents(
                        selectedTorrents.size === localPending.data?.length
                          ? new Set()
                          : new Set(localPending.data?.map((f) => f.id)),
                      )
                    }
                  >
                    {selectedTorrents.size === localPending.data?.length ? 'Deselect all' : 'Select all'}
                  </button>
                  <button
                    className="text-xs text-neutral-400 underline"
                    onClick={() =>
                      setSelectedTorrents(
                        new Set(
                          localPending.data
                            ?.filter((f) => f.filename.toLowerCase().endsWith('.epub'))
                            .map((f) => f.id),
                        ),
                      )
                    }
                  >
                    Select all EPUB
                  </button>
                  <button
                    className="text-xs text-neutral-400 underline"
                    onClick={() =>
                      setSelectedTorrents(
                        new Set(
                          localPending.data
                            ?.filter((f) => f.filename.toLowerCase().endsWith('.mobi'))
                            .map((f) => f.id),
                        ),
                      )
                    }
                  >
                    Select all MOBI
                  </button>
                  <button
                    className="text-xs text-neutral-400 underline"
                    onClick={() =>
                      setSelectedTorrents(
                        new Set(
                          localPending.data
                            ?.filter((f) => f.filename.toLowerCase().endsWith('.txt'))
                            .map((f) => f.id),
                        ),
                      )
                    }
                  >
                    Select all TXT
                  </button>
                  <button
                    className="text-xs text-neutral-400 underline"
                    onClick={() =>
                      setSelectedTorrents(
                        new Set(
                          localPending.data
                            ?.filter((f) => f.filename.toLowerCase().endsWith('.cbz'))
                            .map((f) => f.id),
                        ),
                      )
                    }
                  >
                    Select all CBZ
                  </button>
                  <button
                    className="text-xs text-neutral-400 underline"
                    onClick={() =>
                      setSelectedTorrents(
                        new Set(
                          localPending.data
                            ?.filter((f) => f.filename.toLowerCase().endsWith('.cbr'))
                            .map((f) => f.id),
                        ),
                      )
                    }
                  >
                    Select all CBR
                  </button>
                </div>

                <ul className="max-h-40 divide-y divide-neutral-100 overflow-y-auto text-sm dark:divide-neutral-800">
                  {localPending.data?.map((f) => (
                    <li key={f.id} className="flex items-center gap-3 py-2">
                      <input
                        type="checkbox"
                        checked={selectedTorrents.has(f.id)}
                        onChange={() => toggleTorrentSelected(f.id)}
                      />
                      <span className="min-w-0 flex-1 truncate">{f.filename}</span>
                      <span className="shrink-0 text-xs text-neutral-400">{formatBytes(f.size_bytes)}</span>
                    </li>
                  ))}
                </ul>

                <div className="mt-3 flex items-center gap-2">
                  <button
                    className="ml-auto rounded bg-neutral-900 px-3 py-1.5 text-xs text-white disabled:opacity-50 dark:bg-neutral-100 dark:text-neutral-900"
                    disabled={torrentsBusy || selectedTorrents.size === 0}
                    onClick={handleCopySelectedTorrents}
                  >
                    Copy {selectedTorrents.size || ''} to Book Dump
                  </button>
                  <button
                    className="rounded border border-neutral-300 px-3 py-1.5 text-xs disabled:opacity-50 dark:border-neutral-700"
                    disabled={torrentsBusy || selectedTorrents.size === 0}
                    onClick={handleDismissSelectedTorrents}
                  >
                    Dismiss
                  </button>
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Step 1: Scan */}
      <div className="mt-6">
        <button
          className="rounded bg-neutral-900 px-3 py-1.5 text-sm text-white disabled:opacity-50 dark:bg-neutral-100 dark:text-neutral-900"
          disabled={!readyToScan || scanStarting || scan.data?.status === 'running'}
          onClick={handleStartScan}
        >
          Start scan
        </button>

        {scanError && <p className="mt-2 text-sm text-red-600">{scanError}</p>}

        {scan.data && (
          <p className="mt-2 text-sm text-neutral-500">
            job {scan.data.job_id}: {scan.data.status}
            {scan.data.detail ? ` — ${scan.data.detail}` : ''}
          </p>
        )}

        {scan.data && scan.data.failures.length > 0 && (
          <ul className="mt-2 max-h-40 divide-y divide-neutral-100 overflow-y-auto rounded border border-red-200 text-xs dark:divide-neutral-800 dark:border-red-900">
            {scan.data.failures.map((f, i) => (
              <li key={i} className="flex items-start gap-3 px-2 py-1.5">
                <span className="min-w-0 flex-1 truncate text-neutral-700 dark:text-neutral-300">{f.filename}</span>
                <span className="shrink-0 max-w-[60%] text-right text-red-600 dark:text-red-400">{f.reason}</span>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* Step 2: Review */}
      {reviewCount > 0 && (
        <div className="mt-6 rounded border border-amber-200 p-4 dark:border-amber-900">
          {!showReview ? (
            <div className="flex items-center justify-between gap-4">
              <p className="text-sm">
                {reviewCount} book{reviewCount === 1 ? '' : 's'} need{reviewCount === 1 ? 's' : ''} review.
                Would you like to review {reviewCount === 1 ? 'it' : 'them'} now?
              </p>
              <button
                className="shrink-0 rounded bg-neutral-900 px-3 py-1.5 text-xs text-white dark:bg-neutral-100 dark:text-neutral-900"
                onClick={() => setShowReview(true)}
              >
                Review now
              </button>
            </div>
          ) : (
            <div>
              <div className="mb-3 flex items-center justify-between">
                <h2 className="text-sm font-medium text-neutral-500">Reviewing</h2>
                <button className="text-xs text-neutral-400 underline" onClick={() => setShowReview(false)}>
                  Hide
                </button>
              </div>
              <ReviewQueue embedded />
            </div>
          )}
        </div>
      )}

      {/* Step 3: Duplicates */}
      {duplicateCount > 0 && (
        <div className="mt-6 rounded border border-purple-200 p-4 dark:border-purple-900">
          {!showDuplicates ? (
            <div className="flex items-center justify-between gap-4">
              <p className="text-sm">
                {duplicateCount} duplicate{duplicateCount === 1 ? '' : 's'} found. Would you like to
                clear {duplicateCount === 1 ? 'it' : 'them'} now?
              </p>
              <button
                className="shrink-0 rounded border border-red-300 px-3 py-1.5 text-xs text-red-700 dark:border-red-800 dark:text-red-400"
                onClick={() => setShowDuplicates(true)}
              >
                Clear duplicates
              </button>
            </div>
          ) : (
            <div>
              <div className="mb-3 flex items-center justify-between">
                <h2 className="text-sm font-medium text-neutral-500">Duplicates</h2>
                <button className="text-xs text-neutral-400 underline" onClick={() => setShowDuplicates(false)}>
                  Hide
                </button>
              </div>
              <Duplicates embedded />
            </div>
          )}
        </div>
      )}

      {/* Step 4: Organize */}
      {organizeCount > 0 && (
        <div className="mt-6 rounded border border-emerald-200 p-4 dark:border-emerald-900">
          <div className="flex items-center justify-between gap-4">
            <p className="text-sm">
              {organizeCount} book{organizeCount === 1 ? '' : 's'} ready to organize. Would you like to
              organize {organizeCount === 1 ? 'it' : 'them'} now?
            </p>
            <button
              className="shrink-0 rounded border border-neutral-300 px-3 py-1.5 text-xs disabled:opacity-50 dark:border-neutral-700"
              disabled={organizeStarting || organize.data?.status === 'running'}
              onClick={handleOrganize}
            >
              {organizeStarting || organize.data?.status === 'running'
                ? 'Organizing…'
                : organizeSettings.data?.dry_run
                  ? 'Organize (dry run)'
                  : 'Organize'}
            </button>
          </div>

          {organizeError && <p className="mt-2 text-sm text-red-600">{organizeError}</p>}

          {organize.data && (
            <p className="mt-2 text-sm text-neutral-500">
              job {organize.data.job_id}: {organize.data.status}
              {organize.data.detail ? ` — ${organize.data.detail}` : ''}
            </p>
          )}

          {organize.data && organize.data.failures.length > 0 && (
            <ul className="mt-2 max-h-40 divide-y divide-neutral-100 overflow-y-auto rounded border border-red-200 text-xs dark:divide-neutral-800 dark:border-red-900">
              {organize.data.failures.map((f, i) => (
                <li key={i} className="flex items-start gap-3 px-2 py-1.5">
                  <span className="min-w-0 flex-1 truncate text-neutral-700 dark:text-neutral-300">{f.filename}</span>
                  <span className="shrink-0 max-w-[60%] text-right text-red-600 dark:text-red-400">{f.reason}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {torrentsCount === 0 &&
        reviewCount === 0 &&
        duplicateCount === 0 &&
        organizeCount === 0 &&
        scan.data?.status === 'done' && (
          <p className="mt-6 text-sm text-neutral-500">
            Nothing waiting on you — no new local files, reviews, duplicates, or books ready to organize.
          </p>
        )}

      {/* Manually re-run the checklist after taking action, or on a fresh
          visit before ever scanning — the queries above are live either way,
          this just forces an immediate recheck rather than waiting on the
          next background refetch. */}
      <button
        className="mt-8 text-xs text-neutral-400 underline"
        onClick={() => {
          queryClient.invalidateQueries({ queryKey: ['reviews', 'pending'] })
          queryClient.invalidateQueries({ queryKey: ['duplicates'] })
          queryClient.invalidateQueries({ queryKey: ['files'] })
        }}
      >
        Refresh checklist
      </button>
    </div>
  )
}
