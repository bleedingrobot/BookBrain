export type OrganizeJobState = 'running' | 'done' | 'failed'

export interface OrganizeFailure {
  filename: string
  reason: string
}

export interface OrganizeJobStatus {
  job_id: string
  status: OrganizeJobState
  detail: string | null
  failures: OrganizeFailure[]
}

export type AutoTrashMode = 'off' | 'exact' | 'all'

export interface OrganizeSettings {
  dry_run: boolean
  hold_hours: number
  auto_organize_min_confidence: number
  auto_trash_duplicates: AutoTrashMode
}
