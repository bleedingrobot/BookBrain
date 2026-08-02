export type OrganizeJobState = 'running' | 'done' | 'failed'

export interface OrganizeJobStatus {
  job_id: string
  status: OrganizeJobState
  detail: string | null
}

export interface OrganizeSettings {
  dry_run: boolean
}
