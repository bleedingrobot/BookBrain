export interface NightlyRunInfo {
  status: 'running' | 'success' | 'failed'
  trigger: string
  started_at: string
  finished_at: string | null
  summary: string | null
  error: string | null
}

export interface NightlySettings {
  enabled: boolean
  hour: number
  last_run: NightlyRunInfo | null
}

export type BackupSettings = NightlySettings

export interface LlmTaggingOtherGenre {
  genre: string
  books: string[]
}

export interface LlmTaggingStatus {
  enabled: boolean
  configured: boolean
  full_done: number
  full_pending: number
  // prompts/47 A.1 — genres the approved vocabulary has no slot for.
  other_genres?: LlmTaggingOtherGenre[]
}
