export type FolderMode = 'existing' | 'app_created'

export interface AuthStartResponse {
  authorization_url: string
}

export interface AuthStatus {
  connected: boolean
  scope_mode: string | null
}

export const DRIVE_FILE_SCOPE = 'https://www.googleapis.com/auth/drive.file'
export const DRIVE_SCOPE = 'https://www.googleapis.com/auth/drive'
