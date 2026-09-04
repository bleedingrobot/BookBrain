import type { BookRow as Row } from '../lib/books'
import type { DriveFile } from '../lib/drive'
import type { KoboDevice } from '../lib/settings'
import { Cover } from './Cover'

interface Props {
  row: Row
  allRows: Row[]
  token: string
  selected: boolean
  expanded: boolean
  sentDevices: KoboDevice[]
  koboDevices: KoboDevice[]
  onToggleSelect: (id: string) => void
  onExpand: (id: string) => void
  onSelectMany: (ids: string[], on: boolean) => void
  onSend: (file: DriveFile, device: KoboDevice) => void
  onDownload: (file: DriveFile) => void
}

export function BookRow({
  row,
  allRows,
  token,
  selected,
  expanded,
  sentDevices,
  koboDevices,
  onToggleSelect,
  onExpand,
  onSelectMany,
  onSend,
  onDownload,
}: Props) {
  const seriesPeers = expanded && row.series ? allRows.filter((r) => r.series === row.series) : []
  const authorPeers = expanded && row.author ? allRows.filter((r) => r.author === row.author) : []

  return (
    <>
      <li
        className={`flex flex-wrap items-start gap-x-3 gap-y-2 py-2.5 ${
          expanded ? '' : 'border-b border-neutral-100 dark:border-neutral-800/60'
        }`}
      >
        <input
          type="checkbox"
          className="mt-1 accent-brand-600"
          checked={selected}
          onChange={() => onToggleSelect(row.id)}
        />
        <button
          type="button"
          className="flex min-w-0 flex-1 items-start gap-3 text-left"
          onClick={() => onExpand(row.id)}
        >
          <Cover token={token} driveId={row.id} isbn={row.isbn} />
          <span className="min-w-0 flex-1">
            <span className="block truncate font-medium text-neutral-900 dark:text-neutral-100">
              {row.title}
              {row.author && <span className="ml-2 font-normal text-neutral-500">{row.author}</span>}
            </span>
            {(row.series || sentDevices.length > 0) && (
              <span className="mt-1 flex flex-wrap items-center gap-1.5">
                {row.series && (
                  <span className="badge bg-brand-50 text-brand-700 dark:bg-brand-950/60 dark:text-brand-300">
                    {row.series}
                    {row.seriesNumber && ` #${row.seriesNumber}`}
                  </span>
                )}
                {sentDevices.map((d) => (
                  <span
                    key={d.folderId}
                    className="badge bg-emerald-50 text-emerald-700 dark:bg-emerald-950/50 dark:text-emerald-400"
                  >
                    ✓ {d.label}
                  </span>
                ))}
              </span>
            )}
          </span>
        </button>
        <div className="flex w-full shrink-0 flex-wrap items-center gap-1 pl-7 sm:w-auto sm:pl-0">
          <button className="btn btn-ghost btn-xs" onClick={() => onDownload(row.file)}>
            Download
          </button>
          {koboDevices.map((device) => (
            <button
              key={device.folderId}
              className="btn btn-neutral btn-xs"
              onClick={() => onSend(row.file, device)}
            >
              {koboDevices.length === 1 ? 'Send to Kobo' : `→ ${device.label}`}
            </button>
          ))}
        </div>
      </li>
      {expanded && (
        <li className="mb-1 rounded-lg bg-neutral-100/70 p-3 dark:bg-neutral-800/30">
          <div className="text-xs leading-relaxed text-neutral-600 sm:pl-7 dark:text-neutral-400">
            {row.description ? (
              <p>{row.description}</p>
            ) : (
              <p className="text-neutral-400 italic">No description on file.</p>
            )}
            {row.addedAt && (
              <p className="mt-1.5 text-neutral-400">
                Added {new Date(row.addedAt).toLocaleDateString()}
              </p>
            )}
            {(seriesPeers.length > 1 || authorPeers.length > 1) && (
              <div className="mt-2.5 flex flex-wrap gap-1.5">
                {seriesPeers.length > 1 && (
                  <button
                    className="btn btn-neutral btn-xs"
                    onClick={() => onSelectMany(seriesPeers.map((r) => r.id), true)}
                  >
                    Select all {seriesPeers.length} in {row.series}
                  </button>
                )}
                {authorPeers.length > 1 && (
                  <button
                    className="btn btn-neutral btn-xs"
                    onClick={() => onSelectMany(authorPeers.map((r) => r.id), true)}
                  >
                    Select all {authorPeers.length} by {row.author}
                  </button>
                )}
              </div>
            )}
          </div>
        </li>
      )}
    </>
  )
}
