export type ScanJobState = 'running' | 'done' | 'failed'

export interface ScanJobStatus {
  job_id: string
  status: ScanJobState
  detail: string | null
}
