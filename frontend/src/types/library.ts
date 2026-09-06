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

export interface DescriptionBackfillEstimate {
  books_missing: number
  will_process: number
  cap: number
  estimated_cost_usd: number
}

export interface RebuildEstimate {
  files_to_identify: number
  estimated_cost_usd: number
  estimated: boolean
}

export interface RecentlyOrganizedItem {
  file_id: number
  operation_id: number
  organized_at: string
  filename: string
  title: string | null
  author: string | null
  series: string | null
  series_number: number | null
  confidence: number | null
  current_status: string
  evidence_summary: string
  confirmed: boolean
}

export interface HeldFileItem {
  file_id: number
  filename: string
  title: string | null
  author: string | null
  series: string | null
  series_number: number | null
  confidence: number | null
  evidence_summary: string
  held_since: string
  eligible_at: string
}

export interface RecentlyOrganizedResponse {
  since_hours: number
  hold_hours: number
  organized: RecentlyOrganizedItem[]
  held: HeldFileItem[]
}

export interface MetadataWritebackJobStatus {
  job_id: string
  status: 'running' | 'done' | 'failed'
  dry_run: boolean
  updated: number
  skipped: number
  failed: number
  remaining: number
}
