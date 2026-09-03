import { useState } from 'react'
import type { KoboDevice, PartialSettings, ViewerSettings } from '../lib/settings'

export function SettingsForm({
  initial,
  onSave,
  onCancel,
}: {
  initial: PartialSettings | null
  onSave: (settings: ViewerSettings) => void
  onCancel?: () => void
}) {
  const [googleClientId, setGoogleClientId] = useState(initial?.googleClientId ?? '')
  const [libraryFolderId, setLibraryFolderId] = useState(initial?.libraryFolderId ?? '')
  const [koboDevices, setKoboDevices] = useState<KoboDevice[]>(initial?.koboDevices ?? [])

  function updateDevice(index: number, patch: Partial<KoboDevice>) {
    setKoboDevices((prev) => prev.map((d, i) => (i === index ? { ...d, ...patch } : d)))
  }

  return (
    <div className="mx-auto mt-16 max-w-md p-6">
      <h1 className="text-xl font-semibold">BookBrain Library</h1>
      <p className="mt-2 text-sm text-neutral-500">
        This runs entirely in your browser — nothing is sent anywhere but Google. All values
        below are saved only in this browser's local storage.
      </p>

      <form
        className="mt-6 space-y-4"
        onSubmit={(e) => {
          e.preventDefault()
          if (googleClientId.trim() && libraryFolderId.trim()) {
            const cleanDevices = koboDevices
              .map((d) => ({ label: d.label.trim(), folderId: d.folderId.trim() }))
              .filter((d) => d.label && d.folderId)
            onSave({
              googleClientId: googleClientId.trim(),
              libraryFolderId: libraryFolderId.trim(),
              koboDevices: cleanDevices.length > 0 ? cleanDevices : undefined,
            })
          }
        }}
      >
        <label className="block text-sm">
          <span className="text-neutral-500">Google OAuth Client ID</span>
          <input
            className="mt-1 w-full rounded border border-neutral-300 px-2 py-1.5 text-sm dark:border-neutral-700 dark:bg-neutral-900"
            value={googleClientId}
            onChange={(e) => setGoogleClientId(e.target.value)}
            placeholder="xxxxxxxx.apps.googleusercontent.com"
          />
          <span className="mt-1 block text-xs text-neutral-400">
            From the same Google Cloud project as your main app — add this page's URL to that
            client's "Authorized JavaScript origins".
          </span>
        </label>

        <label className="block text-sm">
          <span className="text-neutral-500">Library folder ID</span>
          <input
            className="mt-1 w-full rounded border border-neutral-300 px-2 py-1.5 text-sm dark:border-neutral-700 dark:bg-neutral-900"
            value={libraryFolderId}
            onChange={(e) => setLibraryFolderId(e.target.value)}
            placeholder="1AbCdEfGhIjKlMnOpQrStUvWxYz"
          />
          <span className="mt-1 block text-xs text-neutral-400">
            The id segment from your Drive library folder's URL:
            drive.google.com/drive/folders/<b>this part</b>
          </span>
        </label>

        <div className="block text-sm">
          <span className="text-neutral-500">Kobo devices (optional)</span>
          <div className="mt-1 space-y-2">
            {koboDevices.map((device, index) => (
              <div key={index} className="flex items-center gap-2">
                <input
                  className="w-24 shrink-0 rounded border border-neutral-300 px-2 py-1.5 text-sm dark:border-neutral-700 dark:bg-neutral-900"
                  value={device.label}
                  onChange={(e) => updateDevice(index, { label: e.target.value })}
                  placeholder="Name"
                />
                <input
                  className="min-w-0 flex-1 rounded border border-neutral-300 px-2 py-1.5 text-sm dark:border-neutral-700 dark:bg-neutral-900"
                  value={device.folderId}
                  onChange={(e) => updateDevice(index, { folderId: e.target.value })}
                  placeholder="Sync folder ID"
                />
                <button
                  type="button"
                  className="shrink-0 text-xs text-neutral-400 underline"
                  onClick={() => setKoboDevices((prev) => prev.filter((_, i) => i !== index))}
                >
                  Remove
                </button>
              </div>
            ))}
          </div>
          <button
            type="button"
            className="mt-2 text-xs text-neutral-500 underline"
            onClick={() => setKoboDevices((prev) => [...prev, { label: '', folderId: '' }])}
          >
            Add Kobo device
          </button>
          <span className="mt-1 block text-xs text-neutral-400">
            One row per eReader. The folder ID is that device's own "Rakuten Kobo" folder (from its
            Google account, shared to this one) — same id-from-URL format as above. Each book gets a
            "Send to <i>Name</i>" button. Leave empty to hide the Kobo buttons entirely.
          </span>
        </div>

        <div className="flex items-center gap-3">
          <button
            type="submit"
            className="rounded bg-neutral-900 px-3 py-1.5 text-sm text-white dark:bg-neutral-100 dark:text-neutral-900"
          >
            Save
          </button>
          {onCancel && (
            <button type="button" className="text-xs text-neutral-400 underline" onClick={onCancel}>
              Cancel
            </button>
          )}
        </div>
      </form>
    </div>
  )
}
