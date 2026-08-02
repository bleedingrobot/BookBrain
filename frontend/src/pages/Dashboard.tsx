import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api, ApiError } from '../services/api'
import { useOrganizeStatus } from '../hooks/useOrganizeStatus'
import { useScanStatus } from '../hooks/useScanStatus'

export function Dashboard() {
  const [jobId, setJobId] = useState<string | null>(null)
  const [scanError, setScanError] = useState<string | null>(null)
  const [organizeJobId, setOrganizeJobId] = useState<string | null>(null)
  const [organizeError, setOrganizeError] = useState<string | null>(null)
  const [scanStarting, setScanStarting] = useState(false)
  const [organizeStarting, setOrganizeStarting] = useState(false)

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

  const readyToScan = authStatus.data?.connected === true && inboxFolder.data != null

  return (
    <div className="p-6">
      <h1 className="text-xl font-semibold">Dashboard</h1>

      <p className="mt-4 text-sm">
        Backend health:{' '}
        {health.isLoading ? 'checking...' : health.isError ? 'unreachable' : health.data?.status}
      </p>

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

      <button
        className="mt-4 rounded bg-neutral-900 px-3 py-1.5 text-sm text-white disabled:opacity-50 dark:bg-neutral-100 dark:text-neutral-900"
        disabled={!readyToScan || scanStarting || scan.data?.status === 'running'}
        onClick={async () => {
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
        }}
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

      <div className="mt-8">
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

        {organizeError && <p className="mt-2 text-sm text-red-600">{organizeError}</p>}

        {organize.data && (
          <p className="mt-2 text-sm text-neutral-500">
            job {organize.data.job_id}: {organize.data.status}
            {organize.data.detail ? ` — ${organize.data.detail}` : ''}
          </p>
        )}
      </div>
    </div>
  )
}
