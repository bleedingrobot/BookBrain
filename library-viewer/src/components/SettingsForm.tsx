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
    <div className="mx-auto max-w-md px-6 py-12">
      <img
        src={`${import.meta.env.BASE_URL}favicon.svg`}
        alt=""
        className="h-9 w-9"
      />
      <h1 className="mt-4 text-xl font-semibold tracking-tight">BookBrain Library</h1>
      <p className="mt-2 text-sm leading-relaxed text-neutral-500">
        This runs entirely in your browser — nothing is sent anywhere but Google. The Client ID and
        library folder are saved only in this browser's local storage. Kobo devices also get a copy
        saved to a small settings file in your Drive library folder, so once one signed-in device
        has them, any other device signed in with access to that library picks them up automatically
        — no retyping the same folder ids twice.
      </p>

      <form
        className="mt-6 space-y-5"
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
        <label className="block">
          <span className="text-sm font-medium">Google OAuth Client ID</span>
          <input
            className="field mt-1.5 w-full"
            value={googleClientId}
            onChange={(e) => setGoogleClientId(e.target.value)}
            placeholder="xxxxxxxx.apps.googleusercontent.com"
          />
          <span className="mt-1 block text-xs text-neutral-400">
            From the same Google Cloud project as your main app — add this page's URL to that
            client's "Authorized JavaScript origins".
          </span>
        </label>

        <label className="block">
          <span className="text-sm font-medium">Library folder ID</span>
          <input
            className="field mt-1.5 w-full"
            value={libraryFolderId}
            onChange={(e) => setLibraryFolderId(e.target.value)}
            placeholder="1AbCdEfGhIjKlMnOpQrStUvWxYz"
          />
          <span className="mt-1 block text-xs text-neutral-400">
            The id segment from your Drive library folder's URL:
            drive.google.com/drive/folders/<b>this part</b>
          </span>
        </label>

        <div>
          <span className="text-sm font-medium">Kobo devices (optional)</span>
          <div className="mt-1.5 space-y-2">
            {koboDevices.map((device, index) => (
              <div key={index} className="flex items-center gap-2">
                <input
                  className="field w-24 shrink-0"
                  value={device.label}
                  onChange={(e) => updateDevice(index, { label: e.target.value })}
                  placeholder="Name"
                />
                <input
                  className="field min-w-0 flex-1"
                  value={device.folderId}
                  onChange={(e) => updateDevice(index, { folderId: e.target.value })}
                  placeholder="Sync folder ID"
                />
                <button
                  type="button"
                  className="btn btn-ghost btn-xs shrink-0"
                  onClick={() => setKoboDevices((prev) => prev.filter((_, i) => i !== index))}
                >
                  Remove
                </button>
              </div>
            ))}
          </div>
          <button
            type="button"
            className="btn btn-neutral btn-xs mt-2"
            onClick={() => setKoboDevices((prev) => [...prev, { label: '', folderId: '' }])}
          >
            + Add Kobo device
          </button>
          <span className="mt-1.5 block text-xs leading-relaxed text-neutral-400">
            One row per eReader. The folder ID is that device's own "Rakuten Kobo" folder (from its
            Google account, shared to this one) — same id-from-URL format as above. Each book gets a
            "Send to <i>Name</i>" button. Leave empty to hide the Kobo buttons entirely.
          </span>
        </div>

        <div className="flex items-center gap-3 pt-1">
          <button type="submit" className="btn btn-primary px-4 py-2 text-sm">
            Save
          </button>
          {onCancel && (
            <button
              type="button"
              className="text-xs text-neutral-400 underline underline-offset-2 hover:text-neutral-600 dark:hover:text-neutral-300"
              onClick={onCancel}
            >
              Cancel
            </button>
          )}
        </div>
      </form>
    </div>
  )
}
