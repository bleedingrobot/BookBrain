export interface LocalFileSummary {
  id: number
  filename: string
  path: string
  size_bytes: number
  matched_title: string | null
  matched_author: string | null
  matched_score: number | null
}

export interface CopyResult {
  copied: number
  failed: number
}

export interface DismissResult {
  dismissed: number
}
