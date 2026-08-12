const CLIENT_ID_KEY = 'bookbrain.googleClientId'
const FOLDER_ID_KEY = 'bookbrain.libraryFolderId'
const KOBO_FOLDER_ID_KEY = 'bookbrain.koboFolderId'

export interface ViewerSettings {
  googleClientId: string
  libraryFolderId: string
  // Absent for view/download-only guests — "Send to Kobo" just doesn't
  // appear for them. Only the owner's own settings need this.
  koboFolderId?: string
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
  if (!partial.googleClientId || !partial.libraryFolderId) return null
  return partial as ViewerSettings
}

export function saveSettings(settings: ViewerSettings): void {
  localStorage.setItem(CLIENT_ID_KEY, settings.googleClientId)
  localStorage.setItem(FOLDER_ID_KEY, settings.libraryFolderId)
  if (settings.koboFolderId) {
    localStorage.setItem(KOBO_FOLDER_ID_KEY, settings.koboFolderId)
  } else {
    localStorage.removeItem(KOBO_FOLDER_ID_KEY)
  }
}

export function clearSettings(): void {
  localStorage.removeItem(CLIENT_ID_KEY)
  localStorage.removeItem(FOLDER_ID_KEY)
  localStorage.removeItem(KOBO_FOLDER_ID_KEY)
}
