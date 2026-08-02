export interface DriveFolder {
  id: string
  name: string
}

export type FileStatusReason = 'multi_parent' | 'no_parent' | null

export interface DriveFileListing {
  id: string
  name: string
  size_bytes: number
  status_reason: FileStatusReason
}

export interface FolderConfig {
  folder_id: string
  folder_name: string
  created_by_app: boolean
}
