export interface EvidenceItem {
  field_name: string
  value: string
  source: string
}

export interface CandidateItem {
  title: string | null
  author: string | null
  series: string | null
  series_number: number | null
  source: string
}

export interface ReviewSummary {
  id: number
  file_id: number
  filename: string
  status: string
  status_reason: string | null
  proposed_title: string | null
  proposed_author: string | null
  computed_confidence: number | null
}

export interface ReviewDetail extends ReviewSummary {
  proposed_json: Record<string, unknown>
  correction_json: Record<string, unknown> | null
  reasoning_summary: string | null
  evidence: EvidenceItem[]
  candidates: CandidateItem[]
}

export interface CorrectReviewRequest {
  title: string
  author?: string | null
  series?: string | null
  series_number?: number | null
  apply_to_similar?: boolean
}
