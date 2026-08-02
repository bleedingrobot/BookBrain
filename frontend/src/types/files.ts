export interface FileSummary {
  id: number
  filename: string
  status: string
  status_reason: string | null
  book_title: string | null
  book_author: string | null
  book_series: string | null
  book_series_number: number | null
  computed_confidence: number | null
  ai_reasoning: string | null
  quality_score: number | null
  discovered_at: string
}
