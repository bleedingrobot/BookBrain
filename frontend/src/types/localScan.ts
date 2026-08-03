export interface LocalFileSummary {
  id: number
  filename: string
  path: string
  size_bytes: number
}

export interface CopyResult {
  copied: number
  failed: number
}

export interface DismissResult {
  dismissed: number
}
