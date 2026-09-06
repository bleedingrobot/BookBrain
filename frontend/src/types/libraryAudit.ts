export interface SimilarNameMember {
  id: number
  name: string
  book_count: number
  file_count: number
}

export interface SimilarNameCluster {
  members: SimilarNameMember[]
}

export interface SimilarCoverPair {
  book_a_id: number
  book_a_title: string
  file_a_name: string
  book_b_id: number
  book_b_title: string
  file_b_name: string
  distance: number
}

export interface LibraryAuditResult {
  similar_series: SimilarNameCluster[]
  similar_authors: SimilarNameCluster[]
  similar_covers: SimilarCoverPair[]
}

export type AuditClusterKind = 'series' | 'author'

export interface DismissedClusterInfo {
  id: number
  kind: AuditClusterKind
  member_ids: number[]
  created_at: string
}

export interface TitleMergeRepairResult {
  books_split: number
  files_moved: number
}
