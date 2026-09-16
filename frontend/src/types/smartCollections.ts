export interface SmartCollection {
  id: number
  name: string
  description: string | null
  rule: string
  created_at: string
  updated_at: string
}

export interface SmartCollectionCreate {
  name: string
  description?: string | null
  rule: string
}

export interface SmartCollectionUpdate {
  name?: string
  description?: string | null
  rule?: string
}

export interface RulePreviewResult {
  count: number
  sample: string[]
}
