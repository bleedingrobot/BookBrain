export interface DuplicateGroup {
  duplicate_file_id: number
  duplicate_filename: string
  quality_score: number | null
  primary_file_id: number | null
  primary_filename: string | null
  sha256: string
}
