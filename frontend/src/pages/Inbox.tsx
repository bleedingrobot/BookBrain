import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api, ApiError } from '../services/api'

const REASON_LABEL: Record<string, string> = {
  multi_parent: 'in multiple folders',
  no_parent: 'no folder',
}

export function Inbox() {
  const files = useQuery({ queryKey: ['drive-files'], queryFn: api.driveFiles })

  if (files.isLoading) {
    return <div className="p-6 text-sm text-neutral-500">Loading...</div>
  }

  if (files.isError) {
    const message =
      files.error instanceof ApiError && files.error.status === 401
        ? 'Not connected to Google Drive yet.'
        : files.error instanceof ApiError && files.error.status === 400
          ? 'No inbox folder configured yet.'
          : 'Failed to load files.'
    return (
      <div className="p-6 text-sm text-neutral-500">
        {message}{' '}
        <Link to="/settings" className="underline">
          Go to Settings
        </Link>
        .
      </div>
    )
  }

  return (
    <div className="p-6">
      <h1 className="text-xl font-semibold">Inbox</h1>
      <p className="mt-1 text-sm text-neutral-500">
        Raw listing from Drive — parsing and identification land in later milestones.
      </p>

      <ul className="mt-4 divide-y divide-neutral-100 text-sm dark:divide-neutral-800">
        {files.data?.map((file) => (
          <li key={file.id} className="flex items-center justify-between py-2">
            <span>{file.name}</span>
            {file.status_reason && (
              <span className="rounded bg-amber-100 px-2 py-0.5 text-xs text-amber-800 dark:bg-amber-900/40 dark:text-amber-300">
                {REASON_LABEL[file.status_reason] ?? file.status_reason}
              </span>
            )}
          </li>
        ))}
        {files.data?.length === 0 && (
          <li className="py-2 text-neutral-400">No EPUBs found in the inbox folder.</li>
        )}
      </ul>
    </div>
  )
}
