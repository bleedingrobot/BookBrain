export interface AcquireStatus {
  enabled: boolean
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
