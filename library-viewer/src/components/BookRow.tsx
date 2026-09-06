import { sendKey, type BookRow as Row, type SendStatus } from '../lib/books'
import type { DriveFile } from '../lib/drive'
import type { SeriesGap } from '../lib/seriesGaps'
import type { KoboDevice } from '../lib/settings'
import { Cover } from './Cover'

interface Props {
  row: Row
  allRows: Row[]
  token: string
  gap: SeriesGap | undefined
  selected: boolean
  expanded: boolean
  sentDevices: KoboDevice[]
  koboDevices: KoboDevice[]
  sendState: Record<string, SendStatus>
  onToggleSelect: (id: string) => void
  onExpand: (id: string) => void
  onSelectMany: (ids: string[], on: boolean) => void
  onSend: (file: DriveFile, device: KoboDevice) => void
  onDownload: (file: DriveFile) => void
  onRead: (row: Row) => void
  onFilterAuthor: (author: string) => void
  onFilterSeries: (series: string) => void
}

const isEpub = (name: string) => name.toLowerCase().endsWith('.epub')

export function BookRow({
  row,
  allRows,
  token,
  gap,
  selected,
  expanded,
  sentDevices,
  koboDevices,
  sendState,
  onToggleSelect,
  onExpand,
  onSelectMany,
  onSend,
  onDownload,
  onRead,
  onFilterAuthor,
  onFilterSeries,
}: Props) {
  const seriesPeers = expanded && row.series ? allRows.filter((r) => r.series === row.series) : []
  const authorPeers = expanded && row.author ? allRows.filter((r) => r.author === row.author) : []

  return (
    <>
      <li
        id={`book-${row.id}`}
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
          className="mt-0.5 shrink-0"
          onClick={() => onExpand(row.id)}
          aria-label="Details"
        >
          <Cover token={token} driveId={row.id} isbn={row.isbn} />
        </button>
        <div className="min-w-0 flex-1">
          <button
            type="button"
            className="block max-w-full truncate text-left font-medium text-neutral-900 dark:text-neutral-100"
            onClick={() => onExpand(row.id)}
          >
            {row.title}
          </button>
          <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1">
            {row.author && (
              <button
                type="button"
                className="text-xs text-neutral-500 hover:text-brand-600 hover:underline dark:hover:text-brand-400"
                onClick={() => onFilterAuthor(row.author!)}
              >
                {row.author}
              </button>
            )}
            {row.series && (
              <button
                type="button"
                className="badge bg-brand-50 text-brand-700 hover:bg-brand-100 dark:bg-brand-950/60 dark:text-brand-300 dark:hover:bg-brand-950"
                onClick={() => onFilterSeries(row.series!)}
              >
                {row.series}
                {row.seriesNumber && ` #${row.seriesNumber}`}
              </button>
            )}
            {sentDevices.map((d) => (
              <span
                key={d.folderId}
                className="badge bg-emerald-50 text-emerald-700 dark:bg-emerald-950/50 dark:text-emerald-400"
              >
                ✓ {d.label}
              </span>
            ))}
          </div>
        </div>
        <div className="flex w-full shrink-0 flex-wrap items-center gap-1 pl-7 sm:w-auto sm:pl-0">
          {isEpub(row.filename) && (
            <button className="btn btn-primary btn-xs" onClick={() => onRead(row)}>
              Read
            </button>
          )}
          <button className="btn btn-ghost btn-xs" onClick={() => onDownload(row.file)}>
            Download
          </button>
          {koboDevices.map((device) => {
            const status = sendState[sendKey(row.file.id, device.folderId)]
            const label =
              status === 'pending'
                ? 'Sending…'
                : status === 'error'
                  ? 'Failed — retry'
                  : koboDevices.length === 1
                    ? 'Send to Kobo'
                    : `→ ${device.label}`
            return (
              <button
                key={device.folderId}
                type="button"
                className={`btn btn-xs ${status === 'error' ? 'btn-danger' : 'btn-neutral'}`}
                disabled={status === 'pending'}
                aria-busy={status === 'pending'}
                onClick={() => onSend(row.file, device)}
              >
                {label}
              </button>
            )
          })}
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
            {gap && gap.missing.length > 0 && row.series && (
              <p className="mt-1.5 text-amber-700 dark:text-amber-500">
                Missing from {row.series}: {gap.missing.map((n) => `#${n}`).join(', ')}
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
