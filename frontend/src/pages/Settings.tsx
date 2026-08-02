import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, ApiError } from '../services/api'
import { DRIVE_FILE_SCOPE, type FolderMode } from '../types/auth'
import { FolderPicker } from '../components/FolderPicker'

export function Settings() {
  const queryClient = useQueryClient()
  const [folderMode, setFolderMode] = useState<FolderMode>('existing')
  const [newFolderName, setNewFolderName] = useState('')
  const [pickError, setPickError] = useState<string | null>(null)
  const [newLibraryFolderName, setNewLibraryFolderName] = useState('')
  const [libraryPickError, setLibraryPickError] = useState<string | null>(null)
  const [confirmingLiveMoves, setConfirmingLiveMoves] = useState(false)

  const authStatus = useQuery({ queryKey: ['auth-status'], queryFn: api.authStatus })
  const inboxFolder = useQuery({
    queryKey: ['inbox-folder'],
    queryFn: api.driveInboxFolder,
    enabled: authStatus.data?.connected === true,
  })
  const libraryFolder = useQuery({
    queryKey: ['library-folder'],
    queryFn: api.driveLibraryFolder,
    enabled: authStatus.data?.connected === true && inboxFolder.data != null,
  })
  const organizeSettings = useQuery({
    queryKey: ['organize-settings'],
    queryFn: api.getOrganizeSettings,
  })
  const systemStatus = useQuery({ queryKey: ['system-status'], queryFn: api.getSystemStatus })

  const disconnect = useMutation({
    mutationFn: api.authDisconnect,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['auth-status'] })
      queryClient.invalidateQueries({ queryKey: ['inbox-folder'] })
      queryClient.invalidateQueries({ queryKey: ['library-folder'] })
    },
  })

  const selectFolder = useMutation({
    mutationFn: (folderId: string) => api.driveSelectInboxFolder(folderId),
    onSuccess: () => {
      setPickError(null)
      queryClient.invalidateQueries({ queryKey: ['inbox-folder'] })
    },
    onError: (err: unknown) => {
      setPickError(err instanceof ApiError ? err.message : 'Failed to select folder.')
    },
  })

  const createInboxFolder = useMutation({
    mutationFn: (name: string) => api.driveCreateInboxFolder(name),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['inbox-folder'] }),
  })

  const selectLibraryFolder = useMutation({
    mutationFn: (folderId: string) => api.driveSelectLibraryFolder(folderId),
    onSuccess: () => {
      setLibraryPickError(null)
      queryClient.invalidateQueries({ queryKey: ['library-folder'] })
    },
    onError: (err: unknown) => {
      setLibraryPickError(err instanceof ApiError ? err.message : 'Failed to select folder.')
    },
  })

  const createLibraryFolder = useMutation({
    mutationFn: (name: string) => api.driveCreateLibraryFolder(name),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['library-folder'] }),
  })

  const updateDryRun = useMutation({
    mutationFn: (dryRun: boolean) => api.updateOrganizeSettings(dryRun),
    onSuccess: (data) => {
      queryClient.setQueryData(['organize-settings'], data)
      setConfirmingLiveMoves(false)
    },
  })

  if (authStatus.isLoading) {
    return <div className="p-6 text-sm text-neutral-500">Loading...</div>
  }

  return (
    <div className="max-w-xl p-6">
      <h1 className="text-xl font-semibold">Settings</h1>

      <section className="mt-6">
        <h2 className="font-medium">Google Drive</h2>

        {!authStatus.data?.connected ? (
          <div className="mt-3 space-y-3 text-sm">
            <p className="text-neutral-500">
              Did you already create the folder EPUBs will live in, or should the app
              create one for you?
            </p>
            <label className="flex items-center gap-2">
              <input
                type="radio"
                checked={folderMode === 'existing'}
                onChange={() => setFolderMode('existing')}
              />
              I already created the folder
            </label>
            <label className="flex items-center gap-2">
              <input
                type="radio"
                checked={folderMode === 'app_created'}
                onChange={() => setFolderMode('app_created')}
              />
              Create a folder for me
            </label>
            <button
              className="rounded bg-neutral-900 px-3 py-1.5 text-white dark:bg-neutral-100 dark:text-neutral-900"
              onClick={async () => {
                const { authorization_url } = await api.authStart(folderMode)
                window.location.href = authorization_url
              }}
            >
              Connect Google Drive
            </button>
          </div>
        ) : (
          <div className="mt-3 space-y-4 text-sm">
            <div className="flex items-center justify-between">
              <p className="text-green-700 dark:text-green-500">Connected.</p>
              <button
                className="rounded border border-neutral-300 px-2 py-1 dark:border-neutral-700"
                onClick={() => disconnect.mutate()}
              >
                Disconnect
              </button>
            </div>

            {inboxFolder.data ? (
              <p>
                Inbox folder: <span className="font-medium">{inboxFolder.data.folder_name}</span>{' '}
                {inboxFolder.data.created_by_app && (
                  <span className="text-neutral-500">(created by app)</span>
                )}
              </p>
            ) : authStatus.data.scope_mode === DRIVE_FILE_SCOPE ? (
              <div className="space-y-2">
                <p className="text-neutral-500">Name the inbox folder the app should create:</p>
                <div className="flex gap-2">
                  <input
                    className="flex-1 rounded border border-neutral-300 px-2 py-1 dark:border-neutral-700 dark:bg-neutral-900"
                    placeholder="EPUB Library"
                    value={newFolderName}
                    onChange={(e) => setNewFolderName(e.target.value)}
                  />
                  <button
                    className="rounded bg-neutral-900 px-3 py-1.5 text-white disabled:opacity-50 dark:bg-neutral-100 dark:text-neutral-900"
                    disabled={newFolderName.trim() === ''}
                    onClick={() => createInboxFolder.mutate(newFolderName.trim())}
                  >
                    Create
                  </button>
                </div>
              </div>
            ) : (
              <div className="space-y-2">
                <p className="text-neutral-500">Pick the folder to use as the inbox:</p>
                {pickError && <p className="text-red-600">{pickError}</p>}
                <FolderPicker onSelect={(folderId) => selectFolder.mutate(folderId)} />
              </div>
            )}
          </div>
        )}
      </section>

      {authStatus.data?.connected && inboxFolder.data && (
        <section className="mt-8">
          <h2 className="font-medium">Library folder</h2>
          <p className="mt-1 text-sm text-neutral-500">
            Where organized books get moved to. Separate from the inbox folder above.
          </p>

          <div className="mt-3 text-sm">
            {libraryFolder.data ? (
              <p>
                Library folder:{' '}
                <span className="font-medium">{libraryFolder.data.folder_name}</span>{' '}
                {libraryFolder.data.created_by_app && (
                  <span className="text-neutral-500">(created by app)</span>
                )}
              </p>
            ) : authStatus.data.scope_mode === DRIVE_FILE_SCOPE ? (
              <div className="space-y-2">
                <p className="text-neutral-500">Name the library folder the app should create:</p>
                <div className="flex gap-2">
                  <input
                    className="flex-1 rounded border border-neutral-300 px-2 py-1 dark:border-neutral-700 dark:bg-neutral-900"
                    placeholder="My Library"
                    value={newLibraryFolderName}
                    onChange={(e) => setNewLibraryFolderName(e.target.value)}
                  />
                  <button
                    className="rounded bg-neutral-900 px-3 py-1.5 text-white disabled:opacity-50 dark:bg-neutral-100 dark:text-neutral-900"
                    disabled={newLibraryFolderName.trim() === ''}
                    onClick={() => createLibraryFolder.mutate(newLibraryFolderName.trim())}
                  >
                    Create
                  </button>
                </div>
              </div>
            ) : (
              <div className="space-y-2">
                <p className="text-neutral-500">Pick the folder to use as the library:</p>
                {libraryPickError && <p className="text-red-600">{libraryPickError}</p>}
                <FolderPicker onSelect={(folderId) => selectLibraryFolder.mutate(folderId)} />
              </div>
            )}
          </div>
        </section>
      )}

      <section className="mt-8">
        <h2 className="font-medium">Organize mode</h2>
        <p className="mt-1 text-sm text-neutral-500">
          Dry run logs what organizing would do without touching Drive. Review a dry-run
          pass before enabling live moves.
        </p>

        {organizeSettings.data && (
          <div className="mt-3 text-sm">
            {organizeSettings.data.dry_run ? (
              <>
                <p className="inline-block rounded bg-amber-100 px-2 py-0.5 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300">
                  Dry run — Drive is never touched
                </p>
                {!confirmingLiveMoves ? (
                  <div>
                    <button
                      className="mt-3 block rounded border border-neutral-300 px-3 py-1.5 dark:border-neutral-700"
                      onClick={() => setConfirmingLiveMoves(true)}
                    >
                      Enable live moves
                    </button>
                  </div>
                ) : (
                  <div className="mt-3 space-y-2 rounded border border-red-300 p-3 dark:border-red-800">
                    <p className="text-red-700 dark:text-red-400">
                      Live moves will actually rename and move files in your Drive library
                      folder. Only enable this after reviewing a dry-run pass in Activity.
                    </p>
                    <div className="flex gap-2">
                      <button
                        className="rounded bg-red-600 px-3 py-1.5 text-white disabled:opacity-50"
                        disabled={updateDryRun.isPending}
                        onClick={() => updateDryRun.mutate(false)}
                      >
                        Yes, enable live moves
                      </button>
                      <button
                        className="rounded border border-neutral-300 px-3 py-1.5 dark:border-neutral-700"
                        onClick={() => setConfirmingLiveMoves(false)}
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                )}
              </>
            ) : (
              <>
                <p className="inline-block rounded bg-red-100 px-2 py-0.5 text-red-800 dark:bg-red-900/40 dark:text-red-300">
                  Live moves enabled
                </p>
                <div>
                  <button
                    className="mt-3 rounded border border-neutral-300 px-3 py-1.5 dark:border-neutral-700"
                    onClick={() => updateDryRun.mutate(true)}
                  >
                    Switch back to dry run
                  </button>
                </div>
              </>
            )}
          </div>
        )}
      </section>

      <section className="mt-8">
        <h2 className="font-medium">System</h2>
        {systemStatus.data && (
          <div className="mt-3 space-y-3 text-sm">
            <div className="flex items-center justify-between">
              <span className="text-neutral-500">Anthropic API key</span>
              <span
                className={
                  systemStatus.data.anthropic_configured
                    ? 'text-green-700 dark:text-green-500'
                    : 'text-red-600 dark:text-red-400'
                }
              >
                {systemStatus.data.anthropic_configured ? 'configured' : 'not configured'}
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-neutral-500">Google Books API key</span>
              <span className="text-neutral-500">
                {systemStatus.data.google_books_configured ? 'configured' : 'not set (optional)'}
              </span>
            </div>
            <div>
              <p className="text-neutral-500">Confidence thresholds</p>
              <ul className="mt-1 space-y-0.5 text-xs text-neutral-400">
                <li>≥ {systemStatus.data.confidence_auto_organize} — auto-organize eligible</li>
                <li>
                  {systemStatus.data.confidence_auto_flagged}–
                  {systemStatus.data.confidence_auto_organize - 1} — auto-organize, flagged in
                  Activity
                </li>
                <li>
                  &lt; {systemStatus.data.confidence_auto_flagged} — sent to the review queue
                  (approve/correct/reject)
                </li>
              </ul>
            </div>
          </div>
        )}
      </section>
    </div>
  )
}
