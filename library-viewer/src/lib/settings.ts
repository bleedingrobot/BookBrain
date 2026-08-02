const CLIENT_ID_KEY = 'bookbrain.googleClientId'
const FOLDER_ID_KEY = 'bookbrain.libraryFolderId'
const KOBO_FOLDER_ID_KEY = 'bookbrain.koboFolderId'

export interface ViewerSettings {
  googleClientId: string
  libraryFolderId: string
  koboFolderId: string
}

export type PartialSettings = Partial<ViewerSettings>

export function loadPartialSettings(): PartialSettings {
  return {
    googleClientId: localStorage.getItem(CLIENT_ID_KEY) ?? undefined,
    libraryFolderId: localStorage.getItem(FOLDER_ID_KEY) ?? undefined,
    koboFolderId: localStorage.getItem(KOBO_FOLDER_ID_KEY) ?? undefined,
  }
}

export function loadSettings(): ViewerSettings | null {
  const partial = loadPartialSettings()
  if (!partial.googleClientId || !partial.libraryFolderId || !partial.koboFolderId) return null
  return partial as ViewerSettings
}

export function saveSettings(settings: ViewerSettings): void {
  localStorage.setItem(CLIENT_ID_KEY, settings.googleClientId)
  localStorage.setItem(FOLDER_ID_KEY, settings.libraryFolderId)
  localStorage.setItem(KOBO_FOLDER_ID_KEY, settings.koboFolderId)
}

export function clearSettings(): void {
  localStorage.removeItem(CLIENT_ID_KEY)
  localStorage.removeItem(FOLDER_ID_KEY)
  localStorage.removeItem(KOBO_FOLDER_ID_KEY)
}
