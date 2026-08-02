export interface OperationSummary {
  id: number
  timestamp: string
  file_id: number
  filename: string
  action: string
  original_name: string | null
  original_parent_id: string | null
  new_name: string | null
  new_parent_id: string | null
  confidence: number | null
  model: string | null
  reason: string | null
  status: string
  dry_run: boolean
}
