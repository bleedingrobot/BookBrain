import { useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { ProgressBar } from '../components/ProgressBar'
import { api, ApiError } from '../services/api'
import { useOrganizeStatus } from '../hooks/useOrganizeStatus'
import { useScanStatus } from '../hooks/useScanStatus'
import { Duplicates } from './Duplicates'
import { ReviewQueue } from './ReviewQueue'

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

  const health = useQuery({ queryKey: ['health'], queryFn: api.health })
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
  const readyToOrganize = useQuery({ queryKey: ['files', 'inbox'], queryFn: () => api.listFiles('inbox') })

  const readyToScan = authStatus.data?.connected === true && inboxFolder.data != null
  const reviewCount = pendingReviews.data?.length ?? 0
  const duplicateCount = duplicates.data?.length ?? 0
  const organizeCount = readyToOrganize.data?.length ?? 0

  const reviewBaseline = useProgressBaseline(reviewCount)
  const duplicateBaseline = useProgressBaseline(duplicateCount)
  const organizeBaseline = useProgressBaseline(organizeCount)

  // A scan can create new reviews/duplicates/organize-ready files — recheck
  // all three the moment it finishes instead of waiting on a manual
  // "Refresh checklist" click or an unrelated background refetch.
  useEffect(() => {
    if (scan.data?.status === 'done') {
      queryClient.invalidateQueries({ queryKey: ['reviews', 'pending'] })
      queryClient.invalidateQueries({ queryKey: ['duplicates'] })
      queryClient.invalidateQueries({ queryKey: ['files'] })
    }
  }, [scan.data?.status, queryClient])

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

  return (
    <div className="p-6">
      <h1 className="text-xl font-semibold">Dashboard</h1>

      <p className="mt-4 text-sm">
        Backend health:{' '}
        {health.isLoading ? 'checking...' : health.isError ? 'unreachable' : health.data?.status}
      </p>

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

          {reviewBaseline === 0 && duplicateBaseline === 0 && organizeBaseline === 0 && !scan.data && (
            <li className="text-xs text-neutral-400">Nothing tracked yet — run a scan to get started.</li>
          )}
        </ul>
      </div>

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
        </div>
      )}

      {reviewCount === 0 && duplicateCount === 0 && organizeCount === 0 && scan.data?.status === 'done' && (
        <p className="mt-6 text-sm text-neutral-500">
          Nothing waiting on you — no reviews, duplicates, or books ready to organize.
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
