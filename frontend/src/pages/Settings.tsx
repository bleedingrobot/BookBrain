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
  const nightly = useQuery({ queryKey: ['nightly-settings'], queryFn: api.getNightlySettings })
  const backups = useQuery({
    queryKey: ['backups'],
    queryFn: api.listBackups,
    enabled: authStatus.data?.connected === true,
  })
  const backupSchedule = useQuery({
    queryKey: ['backup-schedule'],
    queryFn: api.getBackupSchedule,
  })

  const updateBackupSchedule = useMutation({
    mutationFn: ({ enabled, hour }: { enabled: boolean; hour: number }) =>
      api.updateBackupSchedule(enabled, hour),
    onSuccess: (data) => queryClient.setQueryData(['backup-schedule'], data),
  })

  const updateNightly = useMutation({
    mutationFn: ({ enabled, hour }: { enabled: boolean; hour: number }) =>
      api.updateNightlySettings(enabled, hour),
    onSuccess: (data) => queryClient.setQueryData(['nightly-settings'], data),
  })

  const runBackup = useMutation({
    mutationFn: api.createBackup,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['backups'] }),
  })

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

  const updateOrganize = useMutation({
    mutationFn: ({ dryRun, holdHours }: { dryRun: boolean; holdHours: number }) =>
      api.updateOrganizeSettings(dryRun, holdHours),
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
                        disabled={updateOrganize.isPending}
                        onClick={() =>
                          updateOrganize.mutate({
                            dryRun: false,
                            holdHours: organizeSettings.data!.hold_hours,
                          })
                        }
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
                    onClick={() =>
                      updateOrganize.mutate({
                        dryRun: true,
                        holdHours: organizeSettings.data!.hold_hours,
                      })
                    }
                  >
                    Switch back to dry run
                  </button>
                </div>
              </>
            )}

            <div className="mt-5 border-t border-neutral-200 pt-4 dark:border-neutral-800">
              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={organizeSettings.data.hold_hours > 0}
                  disabled={updateOrganize.isPending}
                  onChange={(e) =>
                    updateOrganize.mutate({
                      dryRun: organizeSettings.data!.dry_run,
                      holdHours: e.target.checked ? 24 : 0,
                    })
                  }
                />
                Hold new books before auto-organizing
              </label>
              <p className="mt-1 text-xs text-neutral-500">
                When on, a book that clears the confidence bar waits before it's moved, so
                you can catch a rare wrong identification in the “Recently auto-organized”
                list on the Dashboard first. Off = organize straight away (current
                behaviour).
              </p>
              {organizeSettings.data.hold_hours > 0 && (
                <label className="mt-2 flex items-center gap-2 text-sm">
                  <span className="text-neutral-500">Wait</span>
                  <select
                    className="rounded border border-neutral-300 px-2 py-1 dark:border-neutral-700 dark:bg-neutral-900"
                    value={organizeSettings.data.hold_hours}
                    disabled={updateOrganize.isPending}
                    onChange={(e) =>
                      updateOrganize.mutate({
                        dryRun: organizeSettings.data!.dry_run,
                        holdHours: Number(e.target.value),
                      })
                    }
                  >
                    {[6, 12, 24, 48, 72].map((h) => (
                      <option key={h} value={h}>
                        {h} hours
                      </option>
                    ))}
                  </select>
                </label>
              )}
            </div>
          </div>
        )}
      </section>

      <section className="mt-8">
        <h2 className="font-medium">Nightly run</h2>
        <p className="mt-1 text-sm text-neutral-500">
          Once a night, unattended: pull the Torrents folder, scan the Book Dump,
          auto-organize everything that clears the confidence bar, then refresh covers
          and the library index. Anything uncertain still waits in the review queue.
          Needs the machine awake at that hour; if it's off, the run catches up next
          time the app opens.
        </p>

        {nightly.data && (
          <div className="mt-3 space-y-3 text-sm">
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={nightly.data.enabled}
                disabled={updateNightly.isPending}
                onChange={(e) =>
                  updateNightly.mutate({ enabled: e.target.checked, hour: nightly.data!.hour })
                }
              />
              Run automatically every night
            </label>

            <label className="flex items-center gap-2">
              <span className="text-neutral-500">At</span>
              <select
                className="rounded border border-neutral-300 px-2 py-1 dark:border-neutral-700 dark:bg-neutral-900"
                value={nightly.data.hour}
                disabled={!nightly.data.enabled || updateNightly.isPending}
                onChange={(e) =>
                  updateNightly.mutate({ enabled: nightly.data!.enabled, hour: Number(e.target.value) })
                }
              >
                {Array.from({ length: 24 }, (_, h) => (
                  <option key={h} value={h}>
                    {String(h).padStart(2, '0')}:00
                  </option>
                ))}
              </select>
              <span className="text-neutral-400">machine local time</span>
            </label>

            <div className="text-xs text-neutral-400">
              {nightly.data.last_run ? (
                <>
                  Last run{' '}
                  {new Date(
                    nightly.data.last_run.finished_at ?? nightly.data.last_run.started_at,
                  ).toLocaleString()}
                  {' — '}
                  {nightly.data.last_run.status === 'failed' ? (
                    <span className="text-red-600 dark:text-red-400">
                      failed: {nightly.data.last_run.error}
                    </span>
                  ) : nightly.data.last_run.status === 'running' ? (
                    'running…'
                  ) : (
                    nightly.data.last_run.summary
                  )}
                </>
              ) : (
                'Has not run yet.'
              )}
            </div>
          </div>
        )}
      </section>

      <section className="mt-8">
        <h2 className="font-medium">Backups</h2>
        <p className="mt-1 text-sm text-neutral-500">
          A gzipped snapshot of the database — every book's metadata and every
          correction you've made — plus a portable SQL dump, saved to a{' '}
          <span className="font-mono text-xs">backups/</span> folder in your Drive
          library folder on each nightly run (last 7 kept). Restore steps are in{' '}
          <span className="font-mono text-xs">RESTORE.md</span>.
        </p>

        {backupSchedule.data && (
          <div className="mt-3 space-y-2 text-sm">
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={backupSchedule.data.enabled}
                disabled={updateBackupSchedule.isPending}
                onChange={(e) =>
                  updateBackupSchedule.mutate({
                    enabled: e.target.checked,
                    hour: backupSchedule.data!.hour,
                  })
                }
              />
              Back up automatically every day
            </label>
            <label className="flex items-center gap-2">
              <span className="text-neutral-500">At</span>
              <select
                className="rounded border border-neutral-300 px-2 py-1 dark:border-neutral-700 dark:bg-neutral-900"
                value={backupSchedule.data.hour}
                disabled={!backupSchedule.data.enabled || updateBackupSchedule.isPending}
                onChange={(e) =>
                  updateBackupSchedule.mutate({
                    enabled: backupSchedule.data!.enabled,
                    hour: Number(e.target.value),
                  })
                }
              >
                {Array.from({ length: 24 }, (_, h) => (
                  <option key={h} value={h}>
                    {String(h).padStart(2, '0')}:00
                  </option>
                ))}
              </select>
              <span className="text-neutral-400">machine local time</span>
            </label>
            <p className="text-xs text-neutral-400">
              Independent of the nightly run. Needs the machine awake at that hour; if it's
              off, run <span className="font-mono">python -m app.jobs.backup_job</span> or
              just click below.
            </p>
          </div>
        )}

        <div className="mt-3 space-y-3 text-sm">
          <div className="flex flex-wrap items-center gap-3">
            <button
              className="rounded border border-neutral-300 px-3 py-1.5 disabled:opacity-50 dark:border-neutral-700"
              disabled={runBackup.isPending}
              onClick={() => runBackup.mutate()}
            >
              {runBackup.isPending ? 'Backing up…' : 'Back up now'}
            </button>
            {(() => {
              const newest = backups.data?.[0]
              const daysOld = newest
                ? Math.floor((Date.now() - new Date(newest.created_at).getTime()) / 86_400_000)
                : null
              if (backups.data && backups.data.length === 0)
                return <span className="text-amber-700 dark:text-amber-500">No backups yet.</span>
              if (daysOld !== null && daysOld >= 2)
                return (
                  <span className="text-amber-700 dark:text-amber-500">
                    Last backup {daysOld} days ago.
                  </span>
                )
              return null
            })()}
          </div>

          {runBackup.isError && (
            <p className="text-red-600 dark:text-red-400">
              {runBackup.error instanceof ApiError ? runBackup.error.message : 'Backup failed.'}
            </p>
          )}
          {runBackup.data && (
            <p className="text-neutral-500">
              Saved {runBackup.data.db_name} · {runBackup.data.kept} kept
              {runBackup.data.trashed > 0 ? ` · ${runBackup.data.trashed} old trashed` : ''}
            </p>
          )}

          {backups.data && backups.data.length > 0 && (
            <ul className="divide-y divide-neutral-100 rounded border border-neutral-200 text-xs dark:divide-neutral-800 dark:border-neutral-800">
              {backups.data.map((b) => (
                <li key={b.name} className="flex items-center justify-between gap-3 px-3 py-1.5">
                  <span>{b.created_at}</span>
                  <span className="text-neutral-400">{(b.size_bytes / 1024).toFixed(0)} KB</span>
                  {b.view_url && (
                    <a
                      className="underline"
                      href={b.view_url}
                      target="_blank"
                      rel="noreferrer"
                    >
                      Open in Drive
                    </a>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
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
