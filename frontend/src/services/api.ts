import type { AuthStartResponse, AuthStatus, FolderMode } from '../types/auth'
import type { DriveFileListing, DriveFolder, FolderConfig } from '../types/drive'
import type { ClearDuplicatesResult, DuplicateGroup } from '../types/duplicates'
import type { FileSummary } from '../types/files'
import type {
  BackupInfo,
  BackupResult,
  CoverJobStatus,
  DescriptionBackfillEstimate,
  DescriptionJobStatus,
  LibraryExportResult,
  MetadataWritebackJobStatus,
  RebuildEstimate,
  RecentlyOrganizedResponse,
} from '../types/library'
import type {
  AuditClusterKind,
  DismissedClusterInfo,
  LibraryAuditResult,
  TitleMergeRepairResult,
} from '../types/libraryAudit'
import type {
  DeepCheckEstimate,
  DeepCheckResult,
  ReidentDismissedInfo,
  ReidentRebuildJobStatus,
  ReidentReport,
} from '../types/reidentAudit'
import type { CopyResult, DismissResult, LocalFileSummary } from '../types/localScan'
import type { OperationSummary } from '../types/operations'
import type { OrganizeJobStatus, OrganizeSettings } from '../types/organize'
import type { CorrectReviewRequest, ReviewDetail, ReviewSummary } from '../types/reviews'
import type { SeriesMergeProposal, SeriesMergeResult } from '../types/seriesMerge'
import type {
  ResolveResult,
  WishlistItem,
  WishlistItemCreate,
} from '../types/wishlist'
import type { ScanJobStatus } from '../types/scan'
import type { SystemStatus } from '../types/system'
import type { BackupSettings, NightlySettings } from '../types/jobs'

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
  updateOrganizeSettings: (dryRun: boolean, holdHours: number) =>
    request<OrganizeSettings>('/settings/organize', {
      method: 'PUT',
      body: JSON.stringify({ dry_run: dryRun, hold_hours: holdHours }),
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
  clearOneDuplicate: (fileId: number) =>
    request<ClearDuplicatesResult>(`/duplicates/${fileId}/clear`, { method: 'POST' }),
  unflagDuplicate: (fileId: number) =>
    request<void>(`/duplicates/${fileId}/unflag`, { method: 'POST' }),
  getLibraryAudit: () => request<LibraryAuditResult>('/library-audit'),
  repairTitleMerges: () =>
    request<TitleMergeRepairResult>('/library-audit/repair-title-merges', { method: 'POST' }),
  dismissAuditCluster: (kind: AuditClusterKind, memberIds: number[]) =>
    request<void>('/library-audit/dismiss', {
      method: 'POST',
      body: JSON.stringify({ kind, member_ids: memberIds }),
    }),
  listDismissedClusters: () => request<DismissedClusterInfo[]>('/library-audit/dismissed'),
  restoreDismissedCluster: (id: number) =>
    request<void>(`/library-audit/dismissed/${id}/restore`, { method: 'POST' }),
  getReidentReport: () => request<ReidentReport>('/library-audit/reident'),
  rebuildReidentReport: () =>
    request<ReidentRebuildJobStatus>('/library-audit/reident/rebuild', { method: 'POST' }),
  getReidentRebuildStatus: (jobId: string) =>
    request<ReidentRebuildJobStatus>(`/library-audit/reident/rebuild/${jobId}`),
  estimateReidentDeepCheck: (bookIds: number[]) =>
    request<DeepCheckEstimate>('/library-audit/reident/deep-check/estimate', {
      method: 'POST',
      body: JSON.stringify({ book_ids: bookIds }),
    }),
  runReidentDeepCheck: (bookIds: number[]) =>
    request<DeepCheckResult>('/library-audit/reident/deep-check', {
      method: 'POST',
      body: JSON.stringify({ book_ids: bookIds }),
    }),
  dismissReidentFlag: (bookId: number) =>
    request<void>('/library-audit/reident/dismiss', {
      method: 'POST',
      body: JSON.stringify({ book_id: bookId }),
    }),
  listReidentDismissed: () =>
    request<ReidentDismissedInfo[]>('/library-audit/reident/dismissed'),
  restoreReidentFlag: (bookId: number) =>
    request<void>(`/library-audit/reident/dismissed/${bookId}/restore`, { method: 'POST' }),

  investigateSeriesMerge: (seriesIds: number[]) =>
    request<SeriesMergeProposal>('/library-audit/series/investigate', {
      method: 'POST',
      body: JSON.stringify({ series_ids: seriesIds }),
    }),
  applySeriesMerge: (seriesIds: number[], canonicalSeriesName: string, excludedSeriesNames: string[]) =>
    request<SeriesMergeResult>('/library-audit/series/apply', {
      method: 'POST',
      body: JSON.stringify({
        series_ids: seriesIds,
        canonical_series_name: canonicalSeriesName,
        excluded_series_names: excludedSeriesNames,
        confirm_same_series: true,
      }),
    }),

  listFiles: (status?: string) =>
    request<FileSummary[]>(`/files${status ? `?status=${encodeURIComponent(status)}` : ''}`),
  removeFile: (id: number) => request<void>(`/files/${id}/remove`, { method: 'POST' }),
  correctFile: (id: number, body: CorrectReviewRequest) =>
    request<FileSummary>(`/files/${id}/correct`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  confirmFile: (id: number) =>
    request<FileSummary>(`/files/${id}/confirm`, { method: 'POST' }),
  confirmFiles: (fileIds: number[]) =>
    request<{ confirmed: number; skipped: number }>('/files/confirm-batch', {
      method: 'POST',
      body: JSON.stringify({ file_ids: fileIds }),
    }),
  getRecentlyOrganized: (since: string) =>
    request<RecentlyOrganizedResponse>(
      `/library/recently-organized?since=${encodeURIComponent(since)}`,
    ),

  clearLibrary: () => request<void>('/library/clear', { method: 'POST' }),
  rebuildLibrary: () => request<ScanJobStatus>('/library/rebuild', { method: 'POST' }),
  rebuildEstimate: () => request<RebuildEstimate>('/library/rebuild/estimate'),
  descriptionEstimate: () =>
    request<DescriptionBackfillEstimate>('/library/descriptions/estimate'),
  getRebuildStatus: (jobId: string) => request<ScanJobStatus>(`/library/rebuild/${jobId}`),
  exportLibrary: () => request<LibraryExportResult>('/library/export', { method: 'POST' }),
  createBackup: () => request<BackupResult>('/library/backup', { method: 'POST' }),
  listBackups: () => request<BackupInfo[]>('/library/backups'),
  refreshLibraryIndex: () => request<{ books: number }>('/library/index', { method: 'POST' }),
  generateCovers: () => request<CoverJobStatus>('/library/covers', { method: 'POST' }),
  getCoverStatus: (jobId: string) => request<CoverJobStatus>(`/library/covers/${jobId}`),
  backfillDescriptions: (ai: boolean) =>
    request<DescriptionJobStatus>(`/library/descriptions${ai ? '?ai=true' : ''}`, {
      method: 'POST',
    }),
  getDescriptionStatus: (jobId: string) =>
    request<DescriptionJobStatus>(`/library/descriptions/${jobId}`),
  writeEmbeddedMetadata: (dryRun: boolean) =>
    request<MetadataWritebackJobStatus>(
      `/library/embedded-metadata${dryRun ? '?dry_run=true' : ''}`,
      { method: 'POST' },
    ),
  getEmbeddedMetadataStatus: (jobId: string) =>
    request<MetadataWritebackJobStatus>(`/library/embedded-metadata/${jobId}`),

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

  getNightlySettings: () => request<NightlySettings>('/jobs/nightly'),
  updateNightlySettings: (enabled: boolean, hour: number) =>
    request<NightlySettings>('/jobs/nightly', {
      method: 'PUT',
      body: JSON.stringify({ enabled, hour }),
    }),

  getBackupSchedule: () => request<BackupSettings>('/jobs/backup'),
  updateBackupSchedule: (enabled: boolean, hour: number) =>
    request<BackupSettings>('/jobs/backup', {
      method: 'PUT',
      body: JSON.stringify({ enabled, hour }),
    }),

  scanLocalFolder: () => request<LocalFileSummary[]>('/local-scan', { method: 'POST' }),
  getPendingLocalFiles: () => request<LocalFileSummary[]>('/local-scan/pending'),
  copyLocalFiles: (fileIds: number[]) =>
    request<CopyResult>('/local-scan/copy', { method: 'POST', body: JSON.stringify({ file_ids: fileIds }) }),
  dismissLocalFiles: (fileIds: number[]) =>
    request<DismissResult>('/local-scan/dismiss', { method: 'POST', body: JSON.stringify({ file_ids: fileIds }) }),
}

export { ApiError }
