const CLIENT_ID_KEY = 'bookbrain.googleClientId'
const FOLDER_ID_KEY = 'bookbrain.libraryFolderId'
const KOBO_FOLDER_ID_KEY = 'bookbrain.koboFolderId' // legacy single-folder key, migrated on read
const KOBO_DEVICES_KEY = 'bookbrain.koboDevices'
const READONLY_KEY = 'bookbrain.readOnly'

export interface KoboDevice {
  label: string
  folderId: string
}

export interface ViewerSettings {
  googleClientId: string
  libraryFolderId: string
  // Absent/empty for view/download-only guests — the per-device "Send to
  // Kobo" buttons just don't appear for them. Only the owner's own settings
  // need this. Each entry is one physical eReader: its label and the Drive
  // folder that eReader's native Google Drive sync pulls from (its own
  // account's "Rakuten Kobo" folder, shared to this account).
  koboDevices?: KoboDevice[]
  // Set only for share-link guests: request drive.readonly instead of full
  // Drive, and hide every write action. Never set for the owner.
  readOnly?: boolean
}

export type PartialSettings = Partial<ViewerSettings>

function loadKoboDevices(): KoboDevice[] | undefined {
  const raw = localStorage.getItem(KOBO_DEVICES_KEY)
  if (raw) {
    try {
      const parsed = JSON.parse(raw) as unknown
      if (Array.isArray(parsed)) {
        const clean = parsed.filter(
          (d): d is KoboDevice =>
            !!d && typeof d.label === 'string' && typeof d.folderId === 'string' && !!d.folderId,
        )
        return clean.length > 0 ? clean : undefined
      }
    } catch {
      // fall through to the legacy single-folder key
    }
  }
  // Pre-multi-device installs stored one bare folder id — carry it forward
  // as the owner's own device so nothing breaks before they re-save.
  const legacy = localStorage.getItem(KOBO_FOLDER_ID_KEY)
  return legacy ? [{ label: 'James', folderId: legacy }] : undefined
}

export function loadPartialSettings(): PartialSettings {
  return {
    googleClientId: localStorage.getItem(CLIENT_ID_KEY) ?? undefined,
    libraryFolderId: localStorage.getItem(FOLDER_ID_KEY) ?? undefined,
    koboDevices: loadKoboDevices(),
    readOnly: localStorage.getItem(READONLY_KEY) === '1' || undefined,
  }
}

export function loadSettings(): ViewerSettings | null {
  const partial = loadPartialSettings()
  if (!partial.googleClientId || !partial.libraryFolderId) return null
  return partial as ViewerSettings
}

export function saveSettings(settings: ViewerSettings): void {
  localStorage.setItem(CLIENT_ID_KEY, settings.googleClientId)
  localStorage.setItem(FOLDER_ID_KEY, settings.libraryFolderId)
  if (settings.koboDevices && settings.koboDevices.length > 0) {
    localStorage.setItem(KOBO_DEVICES_KEY, JSON.stringify(settings.koboDevices))
  } else {
    localStorage.removeItem(KOBO_DEVICES_KEY)
  }
  // The legacy key is fully superseded once we've written the new one —
  // drop it so a stale value can't shadow an intentionally-cleared list.
  localStorage.removeItem(KOBO_FOLDER_ID_KEY)
  if (settings.readOnly) localStorage.setItem(READONLY_KEY, '1')
  else localStorage.removeItem(READONLY_KEY)
}

export function clearSettings(): void {
  localStorage.removeItem(CLIENT_ID_KEY)
  localStorage.removeItem(FOLDER_ID_KEY)
  localStorage.removeItem(KOBO_FOLDER_ID_KEY)
  localStorage.removeItem(KOBO_DEVICES_KEY)
  localStorage.removeItem(READONLY_KEY)
}
