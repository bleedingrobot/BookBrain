export interface AcquireStatus {
  enabled: boolean
}

export interface OpenBooksServerStatus {
  installed: boolean
  running: boolean
  managed: boolean
  pid: number | null
}

export interface AcquireBook {
  server: string
  author: string
  title: string
  format: string
  size: string
  full: string
}

export interface AcquireSearchResponse {
  results: AcquireBook[]
  parse_errors: number
  message: string | null
}

export interface AcquireDownloadResponse {
  filename: string
  drive_file_id: string | null
  size_bytes: number
}

export interface RequestCandidate {
  full: string
  title: string | null
  author: string | null
  format: string | null
  size: string | null
  server: string | null
  score: number | null
}

export interface OpenRequest {
  request_id: string
  title: string
  author: string | null
  requested_by: string | null
  cover: string | null
  status: 'pending' | 'approved' | 'skipped' | 'no_match' | 'failed'
  candidate: RequestCandidate | null
  alternatives: RequestCandidate[]
  score: number | null
  message: string | null
  resolved_at: string | null
}

export interface AcquireSuggestion {
  title: string
  author: string | null
  isbn13: string | null
  from_list?: string | null
}

export interface AcquireSuggestions {
  want_to_read: AcquireSuggestion[]
  from_lists: AcquireSuggestion[]
}

export interface RequestRefreshJob {
  job_id: string
  status: 'running' | 'done' | 'failed'
  searched: number
  total: number
  with_candidates: number
  detail: string | null
}
