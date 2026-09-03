import { useEffect, useState } from 'react'
import { listFolderContents, trashFile, type DriveFile } from '../lib/drive'
import { parseFilename } from '../lib/parseFilename'
import type { KoboDevice } from '../lib/settings'

function DeviceSection({
  token,
  device,
  onRemoved,
}: {
  token: string
  device: KoboDevice
  onRemoved: (folderId: string, filename: string) => void
}) {
  const [files, setFiles] = useState<DriveFile[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [removing, setRemoving] = useState<Set<string>>(new Set())
  const [filter, setFilter] = useState('')
  const [reloadKey, setReloadKey] = useState(0)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    listFolderContents(token, device.folderId)
      .then((contents) => {
        if (cancelled) return
        contents.sort((a, b) => a.name.localeCompare(b.name))
        setFiles(contents)
      })
      .catch((err: unknown) => {
        if (!cancelled)
          setError(err instanceof Error ? err.message : 'Failed to load folder contents.')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [device.folderId, token, reloadKey])

  async function remove(file: DriveFile) {
    setRemoving((prev) => new Set(prev).add(file.id))
    setError(null)
    try {
      await trashFile(token, file.id)
      setFiles((prev) => (prev ? prev.filter((f) => f.id !== file.id) : prev))
      onRemoved(device.folderId, file.name)
    } catch (err) {
      setError(err instanceof Error ? err.message : `Failed to remove ${file.name}.`)
    } finally {
      setRemoving((prev) => {
        const next = new Set(prev)
        next.delete(file.id)
        return next
      })
    }
  }

  const shown = (files ?? []).filter((f) =>
    filter.trim() ? f.name.toLowerCase().includes(filter.trim().toLowerCase()) : true,
  )

  return (
    <section className="card mt-4 p-4">
      <div className="flex items-baseline justify-between gap-3">
        <h2 className="text-sm font-semibold">
          {device.label}
          {files && (
            <span className="ml-2 font-normal text-neutral-400">
              {files.length} file{files.length === 1 ? '' : 's'}
            </span>
          )}
        </h2>
        <button
          className="btn btn-ghost btn-xs -mr-1"
          disabled={loading}
          onClick={() => setReloadKey((k) => k + 1)}
        >
          {loading ? 'Loading…' : 'Reload'}
        </button>
      </div>

      {error && <p className="mt-2 text-xs text-red-600">{error}</p>}

      {files && files.length > 3 && (
        <input
          className="field mt-2 w-full"
          placeholder="Filter…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        />
      )}

      <ul className="mt-1 text-sm">
        {!loading && files && files.length === 0 && (
          <li className="py-3 text-neutral-400">Nothing in this device's sync folder.</li>
        )}
        {shown.map((file) => {
          const parsed = parseFilename(file.name)
          return (
            <li
              key={file.id}
              className="flex items-center gap-3 border-b border-neutral-100 py-2 last:border-0 dark:border-neutral-800/60"
            >
              <div className="min-w-0 flex-1">
                <div className="truncate">{parsed.title}</div>
                {parsed.author && (
                  <div className="truncate text-xs text-neutral-400">{parsed.author}</div>
                )}
              </div>
              <button
                className="btn btn-danger btn-xs"
                disabled={removing.has(file.id)}
                onClick={() => void remove(file)}
              >
                {removing.has(file.id) ? 'Removing…' : 'Remove'}
              </button>
            </li>
          )
        })}
      </ul>
    </section>
  )
}

export function DeviceLibrary({
  token,
  devices,
  onBack,
  onRemoved,
}: {
  token: string
  devices: KoboDevice[]
  onBack: () => void
  onRemoved: (folderId: string, filename: string) => void
}) {
  return (
    <div className="mx-auto max-w-2xl px-4 py-5 sm:px-6">
      <button
        className="text-xs text-neutral-400 underline underline-offset-2 hover:text-neutral-600 dark:hover:text-neutral-300"
        onClick={onBack}
      >
        &larr; Back to library
      </button>

      <h1 className="mt-3 text-xl font-semibold tracking-tight">What's on each device</h1>
      <p className="mt-2 text-sm leading-relaxed text-neutral-500">
        The books currently sitting in each Kobo's Google Drive sync folder. Removing one here
        trashes it from Drive (recoverable from Drive's Trash) — the eReader drops it on its next
        sync.
      </p>

      {devices.map((device) => (
        <DeviceSection
          key={device.folderId}
          token={token}
          device={device}
          onRemoved={onRemoved}
        />
      ))}
    </div>
  )
}
