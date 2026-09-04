export interface LibraryExportResult {
  name: string
  url: string
}

export interface CoverJobStatus {
  job_id: string
  status: 'running' | 'done' | 'failed'
  generated: number
  no_cover: number
  failed: number
  remaining: number
}

export interface DescriptionJobStatus {
  job_id: string
  status: 'running' | 'done' | 'failed'
  from_provider: number
  from_ai: number
  not_found: number
  remaining: number
}
