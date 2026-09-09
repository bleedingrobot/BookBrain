import { DEFAULT_GOOGLE_CLIENT_ID, DEFAULT_LIBRARY_FOLDER_ID } from './config'

const CLIENT_ID_KEY = 'bookbrain.googleClientId'
const FOLDER_ID_KEY = 'bookbrain.libraryFolderId'
const KOBO_FOLDER_ID_KEY = 'bookbrain.koboFolderId' // legacy single-folder key, migrated on read
const KOBO_DEVICES_KEY = 'bookbrain.koboDevices'
const SHOW_GLOBAL_RELEASES_KEY = 'bookbrain.showGlobalReleases'
const SHOW_TRENDING_KEY = 'bookbrain.showTrending'
const SHOW_NEWS_KEY = 'bookbrain.showNews' // default ON — stores 'false' only when hidden

export interface KoboDevice {
  label: string
  folderId: string
}

export interface ViewerSettings {
  googleClientId: string
  libraryFolderId: string
  // One entry per physical eReader: its label and the Drive folder that
  // eReader's native Google Drive sync pulls from (its own account's
  // "Rakuten Kobo" folder, shared to this account). Synced to Drive as
  // bookbrain-viewer-settings.json so every signed-in device shares one
  // list — see koboDeviceSync.ts.
  koboDevices?: KoboDevice[]
  // prompts/27 Part 3 — show the "Most anticipated" strip (Hardcover's most
  // wanted upcoming books overall, not filtered to the library). Off by
  // default; browser-local, not Drive-synced.
  showGlobalReleases?: boolean
  // prompts/31 Part G — the "Trending on Hardcover" strip. Off by default;
  // browser-local, not Drive-synced.
  showTrending?: boolean
  // prompts/32 — the "From around the SFF world" news section. On by default;
  // browser-local, not Drive-synced.
  showNews?: boolean
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

// localStorage wins if set (a device pointed at a different library via the
// setup form or a share link), otherwise the build-time defaults so the
// deployed viewer works with no setup at all.
export function loadPartialSettings(): PartialSettings {
  return {
    googleClientId: localStorage.getItem(CLIENT_ID_KEY) ?? DEFAULT_GOOGLE_CLIENT_ID ?? undefined,
    libraryFolderId: localStorage.getItem(FOLDER_ID_KEY) ?? DEFAULT_LIBRARY_FOLDER_ID ?? undefined,
    koboDevices: loadKoboDevices(),
    showGlobalReleases: localStorage.getItem(SHOW_GLOBAL_RELEASES_KEY) === 'true',
    showTrending: localStorage.getItem(SHOW_TRENDING_KEY) === 'true',
    showNews: localStorage.getItem(SHOW_NEWS_KEY) !== 'false',
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
  if (settings.showGlobalReleases) localStorage.setItem(SHOW_GLOBAL_RELEASES_KEY, 'true')
  else localStorage.removeItem(SHOW_GLOBAL_RELEASES_KEY)
  if (settings.showTrending) localStorage.setItem(SHOW_TRENDING_KEY, 'true')
  else localStorage.removeItem(SHOW_TRENDING_KEY)
  // default-on: only persist the opt-out
  if (settings.showNews === false) localStorage.setItem(SHOW_NEWS_KEY, 'false')
  else localStorage.removeItem(SHOW_NEWS_KEY)
}

export function clearSettings(): void {
  localStorage.removeItem(CLIENT_ID_KEY)
  localStorage.removeItem(FOLDER_ID_KEY)
  localStorage.removeItem(KOBO_FOLDER_ID_KEY)
  localStorage.removeItem(KOBO_DEVICES_KEY)
  localStorage.removeItem(SHOW_GLOBAL_RELEASES_KEY)
  localStorage.removeItem(SHOW_TRENDING_KEY)
  localStorage.removeItem(SHOW_NEWS_KEY)
  localStorage.removeItem('bookbrain.readOnly') // orphan from the removed guest mode
}
