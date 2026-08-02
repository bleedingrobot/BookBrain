const CLIENT_ID_KEY = 'bookbrain.googleClientId'
const FOLDER_ID_KEY = 'bookbrain.libraryFolderId'

export interface ViewerSettings {
  googleClientId: string
  libraryFolderId: string
}

export function loadSettings(): ViewerSettings | null {
  const googleClientId = localStorage.getItem(CLIENT_ID_KEY)
  const libraryFolderId = localStorage.getItem(FOLDER_ID_KEY)
  if (!googleClientId || !libraryFolderId) return null
  return { googleClientId, libraryFolderId }
}

export function saveSettings(settings: ViewerSettings): void {
  localStorage.setItem(CLIENT_ID_KEY, settings.googleClientId)
  localStorage.setItem(FOLDER_ID_KEY, settings.libraryFolderId)
}

export function clearSettings(): void {
  localStorage.removeItem(CLIENT_ID_KEY)
  localStorage.removeItem(FOLDER_ID_KEY)
}
