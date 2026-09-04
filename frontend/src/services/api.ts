import type { AuthStartResponse, AuthStatus, FolderMode } from '../types/auth'
import type { DriveFileListing, DriveFolder, FolderConfig } from '../types/drive'
import type { ClearDuplicatesResult, DuplicateGroup } from '../types/duplicates'
import type { FileSummary } from '../types/files'
import type {
  CoverJobStatus,
  DescriptionJobStatus,
  LibraryExportResult,
} from '../types/library'
import type { LibraryAuditResult } from '../types/libraryAudit'
import type { CopyResult, DismissResult, LocalFileSummary } from '../types/localScan'
import type { OperationSummary } from '../types/operations'
import type { OrganizeJobStatus, OrganizeSettings } from '../types/organize'
import type { CorrectReviewRequest, ReviewDetail, ReviewSummary } from '../types/reviews'
import type {
  ResolveResult,
  WishlistItem,
  WishlistItemCreate,
} from '../types/wishlist'
import type { ScanJobStatus } from '../types/scan'
import type { SystemStatus } from '../types/system'

class ApiError extends Error {
  status: number

  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    throw new ApiError(body?.detail ?? `${init?.method ?? 'GET'} ${path} failed`, response.status)
  }
  if (response.status === 204) {
    return undefined as T
  }
  return response.json() as Promise<T>
}

export const api = {
  health: () => request<{ status: string }>('/health'),
  startScan: () => request<ScanJobStatus>('/scan', { method: 'POST' }),
  getScanStatus: (jobId: string) => request<ScanJobStatus>(`/scan/${jobId}`),

  authStart: (folderMode: FolderMode) =>
    request<AuthStartResponse>('/auth/start', {
      method: 'POST',
      body: JSON.stringify({ folder_mode: folderMode }),
    }),
  authStatus: () => request<AuthStatus>('/auth/status'),
  authDisconnect: () => request<void>('/auth/disconnect', { method: 'POST' }),

  driveFolders: (parentId?: string) =>
    request<DriveFolder[]>(`/drive/folders${parentId ? `?parent_id=${parentId}` : ''}`),
  driveCreateFolder: (name: string, parentId?: string) =>
    request<DriveFolder>('/drive/folders', {
      method: 'POST',
      body: JSON.stringify({ name, parent_id: parentId ?? null }),
    }),
  driveInboxFolder: () => request<FolderConfig | null>('/drive/inbox-folder'),
  driveSelectInboxFolder: (folderId: string) =>
    request<FolderConfig>('/drive/inbox-folder/select', {
      method: 'POST',
      body: JSON.stringify({ folder_id: folderId }),
    }),
  driveCreateInboxFolder: (name: string) =>
    request<FolderConfig>('/drive/inbox-folder/create', {
      method: 'POST',
      body: JSON.stringify({ name }),
    }),
  driveFiles: () => request<DriveFileListing[]>('/drive/files'),

  driveLibraryFolder: () => request<FolderConfig | null>('/drive/library-folder'),
  driveSelectLibraryFolder: (folderId: string) =>
    request<FolderConfig>('/drive/library-folder/select', {
      method: 'POST',
      body: JSON.stringify({ folder_id: folderId }),
    }),
  driveCreateLibraryFolder: (name: string) =>
    request<FolderConfig>('/drive/library-folder/create', {
      method: 'POST',
      body: JSON.stringify({ name }),
    }),

  getOrganizeSettings: () => request<OrganizeSettings>('/settings/organize'),
  updateOrganizeSettings: (dryRun: boolean) =>
    request<OrganizeSettings>('/settings/organize', {
      method: 'PUT',
      body: JSON.stringify({ dry_run: dryRun }),
    }),
  startOrganize: () => request<OrganizeJobStatus>('/organize', { method: 'POST' }),
  getOrganizeStatus: (jobId: string) => request<OrganizeJobStatus>(`/organize/${jobId}`),

  listReviews: (status = 'pending') =>
    request<ReviewSummary[]>(`/reviews?status=${encodeURIComponent(status)}`),
  getReview: (id: number) => request<ReviewDetail>(`/reviews/${id}`),
  approveReview: (id: number) => request<ReviewDetail>(`/reviews/${id}/approve`, { method: 'POST' }),
  correctReview: (id: number, body: CorrectReviewRequest) =>
    request<ReviewDetail>(`/reviews/${id}/correct`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  rejectReview: (id: number) => request<ReviewDetail>(`/reviews/${id}/reject`, { method: 'POST' }),

  listDuplicates: () => request<DuplicateGroup[]>('/duplicates'),
  clearDuplicates: () => request<ClearDuplicatesResult>('/duplicates/clear', { method: 'POST' }),
  getLibraryAudit: () => request<LibraryAuditResult>('/library-audit'),

  listFiles: (status?: string) =>
    request<FileSummary[]>(`/files${status ? `?status=${encodeURIComponent(status)}` : ''}`),
  removeFile: (id: number) => request<void>(`/files/${id}/remove`, { method: 'POST' }),
  correctFile: (id: number, body: CorrectReviewRequest) =>
    request<FileSummary>(`/files/${id}/correct`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  clearLibrary: () => request<void>('/library/clear', { method: 'POST' }),
  rebuildLibrary: () => request<ScanJobStatus>('/library/rebuild', { method: 'POST' }),
  getRebuildStatus: (jobId: string) => request<ScanJobStatus>(`/library/rebuild/${jobId}`),
  exportLibrary: () => request<LibraryExportResult>('/library/export', { method: 'POST' }),
  refreshLibraryIndex: () => request<{ books: number }>('/library/index', { method: 'POST' }),
  generateCovers: () => request<CoverJobStatus>('/library/covers', { method: 'POST' }),
  getCoverStatus: (jobId: string) => request<CoverJobStatus>(`/library/covers/${jobId}`),
  backfillDescriptions: (ai: boolean) =>
    request<DescriptionJobStatus>(`/library/descriptions${ai ? '?ai=true' : ''}`, {
      method: 'POST',
    }),
  getDescriptionStatus: (jobId: string) =>
    request<DescriptionJobStatus>(`/library/descriptions/${jobId}`),

  resolveWishlist: (text: string) =>
    request<ResolveResult>('/wishlist/resolve', {
      method: 'POST',
      body: JSON.stringify({ text }),
    }),
  listWishlist: () => request<WishlistItem[]>('/wishlist'),
  addWishlist: (body: WishlistItemCreate) =>
    request<WishlistItem>('/wishlist', { method: 'POST', body: JSON.stringify(body) }),
  setWishlistStatus: (id: number, status: 'wanted' | 'acquired') =>
    request<WishlistItem>(`/wishlist/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({ status }),
    }),
  deleteWishlist: (id: number) => request<void>(`/wishlist/${id}`, { method: 'DELETE' }),

  listOperations: () => request<OperationSummary[]>('/operations'),
  undoOperation: (id: number) => request<OperationSummary>(`/operations/${id}/undo`, { method: 'POST' }),

  getSystemStatus: () => request<SystemStatus>('/settings/status'),

  scanLocalFolder: () => request<LocalFileSummary[]>('/local-scan', { method: 'POST' }),
  getPendingLocalFiles: () => request<LocalFileSummary[]>('/local-scan/pending'),
  copyLocalFiles: (fileIds: number[]) =>
    request<CopyResult>('/local-scan/copy', { method: 'POST', body: JSON.stringify({ file_ids: fileIds }) }),
  dismissLocalFiles: (fileIds: number[]) =>
    request<DismissResult>('/local-scan/dismiss', { method: 'POST', body: JSON.stringify({ file_ids: fileIds }) }),
}

export { ApiError }
