export type ReidentSignal =
  | 'series_unverified'
  | 'title_disagrees'
  | 'author_disagrees'
  | 'isbn_points_elsewhere'
  | 'below_auto_organize'
  | 'possible_duplicate'

export interface ReidentDivergence {
  book_id: number
  file_id: number
  filename: string
  stored_title: string
  stored_author: string | null
  stored_series: string | null
  stored_series_number: number | null
  stored_confidence: number | null
  stored_from_human: boolean
  signals: ReidentSignal[]
  evidence: string[]
  recomputed_confidence: number | null
  duplicate_of_book_id: number | null
  deep_check_verdict: string | null
  deep_check_explanation: string | null
  deep_check_suggested_title: string | null
  deep_check_suggested_author: string | null
  deep_check_suggested_series: string | null
  deep_check_suggested_series_number: number | null
}

export interface ReidentReport {
  generated_at: string | null
  total_organised_books: number
  checked: number
  providers_unavailable: number
  divergences: ReidentDivergence[]
}

export interface ReidentRebuildJobStatus {
  job_id: string
  status: 'running' | 'done' | 'failed'
  checked: number
  total: number
  flagged: number
  detail: string | null
}

export interface ReidentDismissedInfo {
  book_id: number
  created_at: string
}

export interface DeepCheckEstimate {
  eligible: number
  will_check: number
  cap: number
  estimated_cost_usd: number
}

export interface DeepCheckRow {
  book_id: number
  verdict: string
  explanation: string
  suggested_title: string | null
  suggested_author: string | null
  suggested_series: string | null
  suggested_series_number: number | null
}

export interface DeepCheckResult {
  rechecked: number
  stored_is_wrong: number
  stored_is_correct: number
  uncertain: number
  failed: number
  rows: DeepCheckRow[]
}

export const SIGNAL_LABEL: Record<ReidentSignal, string> = {
  series_unverified: 'Series unverified',
  title_disagrees: 'Title disagrees',
  author_disagrees: 'Author disagrees',
  isbn_points_elsewhere: 'ISBN points elsewhere',
  below_auto_organize: 'Low confidence',
  possible_duplicate: 'Possible duplicate',
}
