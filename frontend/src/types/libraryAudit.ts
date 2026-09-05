export interface SimilarNameMember {
  id: number
  name: string
  book_count: number
  file_count: number
}

export interface SimilarNameCluster {
  members: SimilarNameMember[]
}

export interface LibraryAuditResult {
  similar_series: SimilarNameCluster[]
  similar_authors: SimilarNameCluster[]
}

export type AuditClusterKind = 'series' | 'author'

export interface DismissedClusterInfo {
  id: number
  kind: AuditClusterKind
  member_ids: number[]
  created_at: string
}
