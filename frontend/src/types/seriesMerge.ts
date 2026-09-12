export interface SeriesMergeBookInfo {
  id: number
  canonical_title: string
  series_number: number | null
  author_name: string | null
  file_count: number
}

export interface SeriesMergeSeriesInfo {
  id: number
  name: string
  books: SeriesMergeBookInfo[]
}

export interface SeriesMergePlannedMove {
  book_title: string
  from_series_name: string
  current_filename: string
  new_filename: string
  new_folder_path: string
}

export interface SeriesMergePlan {
  moves: SeriesMergePlannedMove[]
  series_to_delete: string[]
}

export interface SeriesMergeProposal {
  is_same_series: boolean
  canonical_series_name: string
  excluded_series_names: string[]
  confidence: number
  explanation: string
  warnings: string[]
  series: SeriesMergeSeriesInfo[]
  plan: SeriesMergePlan
}

export interface SeriesMergeFileFailure {
  file_id: number
  filename: string
  reason: string
}

export interface SeriesMergeBookSkip {
  book_id: number
  canonical_title: string
  reason: string
}

export interface SeriesMergeResult {
  canonical_series_id: number
  canonical_series_name: string
  moved_files: number
  already_in_place_files: number
  failed_files: SeriesMergeFileFailure[]
  repointed_books: number
  skipped_books: SeriesMergeBookSkip[]
  deleted_series_ids: number[]
}
