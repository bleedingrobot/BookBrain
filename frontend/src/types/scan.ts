export type ScanJobState = 'running' | 'done' | 'failed'

export interface ScanFailure {
  filename: string
  reason: string
}

export interface ScanJobStatus {
  job_id: string
  status: ScanJobState
  detail: string | null
  failures: ScanFailure[]
}
