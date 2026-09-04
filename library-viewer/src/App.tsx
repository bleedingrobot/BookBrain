import { useMemo, useState } from 'react'
import { BookList } from './components/BookList'
import { DeviceLibrary } from './components/DeviceLibrary'
import { LibraryHeader } from './components/LibraryHeader'
import { SettingsForm } from './components/SettingsForm'
import { SetupChecklist } from './components/SetupChecklist'
import {
  buildRows,
  matchesFilter,
  matchesRow,
  SORT_LABELS,
  SORTS,
  type FilterKey,
  type SortKey,
} from './lib/books'
import { copyFileToFolder, downloadFile, type DriveFile } from './lib/drive'
import { clearSentTracker, getSentMap, markSent, unmarkSent } from './lib/sentTracker'
import {
  clearSettings,
  loadPartialSettings,
  loadSettings,
  saveSettings,
  type KoboDevice,
  type ViewerSettings,
} from './lib/settings'
import { useLibrary } from './hooks/useLibrary'

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

// A share link (?clientId=...&folderId=...) hands a guest a ready-to-use,
// read-only config with no Kobo access — they never see the settings form
// at all. Consumed once on load, then scrubbed from the address bar so the
// values don't linger in browser history.
function consumeSharedSettings(): ViewerSettings | null {
  const params = new URLSearchParams(window.location.search)
  const googleClientId = params.get('clientId')
  const libraryFolderId = params.get('folderId')
  if (!googleClientId || !libraryFolderId) return null

  const shared: ViewerSettings = { googleClientId, libraryFolderId, readOnly: true }
  saveSettings(shared)
  window.history.replaceState({}, '', window.location.pathname)
  return shared
}

export default function App() {
  const [showSetup, setShowSetup] = useState(false)
  const [showDevices, setShowDevices] = useState(false)
  const [editingSettings, setEditingSettings] = useState(false)
  const [settings, setSettings] = useState<ViewerSettings | null>(
    () => consumeSharedSettings() ?? loadSettings(),
  )
  const [shareStatus, setShareStatus] = useState<string | null>(null)

  const lib = useLibrary(settings)
  const { token, files, index } = lib

  const [query, setQuery] = useState('')
  const [sort, setSort] = useState<SortKey>('title')
  const [filter, setFilter] = useState<FilterKey>('all')
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [downloading, setDownloading] = useState(false)
  const [downloadError, setDownloadError] = useState<string | null>(null)
  const [sendingToKobo, setSendingToKobo] = useState(false)
  const [koboError, setKoboError] = useState<string | null>(null)
  const [koboMessage, setKoboMessage] = useState<string | null>(null)
  const [sentMap, setSentMap] = useState(getSentMap)

  const readOnly = settings?.readOnly ?? false
  const koboDevices = readOnly ? [] : (settings?.koboDevices ?? [])
  const hasKobo = koboDevices.length > 0

  const allRows = useMemo(() => buildRows(files ?? [], index), [files, index])
  const rows = useMemo(() => {
    const out = allRows.filter(
      (row) => matchesRow(row, query) && matchesFilter(row, filter, sentMap),
    )
    out.sort(SORTS[sort])
    return out
  }, [allRows, query, sort, filter, sentMap])

  function selectMany(ids: string[], on: boolean) {
    setSelected((prev) => {
      const next = new Set(prev)
      for (const id of ids) {
        if (on) next.add(id)
        else next.delete(id)
      }
      return next
    })
  }

  function toggleSelected(id: string) {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  function shareLink(): { link: string; message: string } | null {
    if (!settings) return null
    const link = `${window.location.origin}${window.location.pathname}?clientId=${encodeURIComponent(settings.googleClientId)}&folderId=${encodeURIComponent(settings.libraryFolderId)}`
    return {
      link,
      message: `You're invited to browse and download books from the BookBrain library. Open this link and sign in with your Google account:\n${link}`,
    }
  }

  async function handleShare() {
    const share = shareLink()
    if (!share) return
    if (navigator.share) {
      try {
        await navigator.share({ title: 'BookBrain Library', text: share.message })
      } catch {
        /* cancelled */
      }
      return
    }
    await handleCopyLink()
  }

  async function handleCopyLink() {
    const share = shareLink()
    if (!share) return
    try {
      await navigator.clipboard.writeText(share.message)
      setShareStatus('Copied! Paste it into a message to send.')
    } catch {
      setShareStatus(`Copy failed — here's the link: ${share.link}`)
    }
    setTimeout(() => setShareStatus(null), 5000)
  }

  function handleForget() {
    clearSettings()
    clearSentTracker()
    lib.reset()
    setSentMap({})
    setSettings(null)
  }

  function handleDeviceRemoval(folderId: string, filename: string) {
    const match = allRows.find((r) => r.filename === filename)
    if (match) setSentMap(unmarkSent(folderId, [match.id]))
  }

  // The device screen reports each folder's real contents; sync the local
  // "✓ on device" ticks to match — add present books, drop absent ones,
  // matched by filename (the copy keeps the library file's name).
  function handleDeviceReconcile(folderId: string, filenames: string[]) {
    const present = new Set(filenames)
    const shouldBeSent = allRows.filter((r) => present.has(r.filename)).map((r) => r.id)
    const staleIds = Object.keys(sentMap[folderId] ?? {}).filter((id) => {
      const row = allRows.find((r) => r.id === id)
      return row ? !present.has(row.filename) : false
    })
    let next = markSent(folderId, shouldBeSent)
    if (staleIds.length > 0) next = unmarkSent(folderId, staleIds)
    setSentMap(next)
  }

  async function handleDownloadSelected() {
    if (!token) return
    setDownloading(true)
    setDownloadError(null)
    const toDownload = allRows.filter((r) => selected.has(r.id))
    const stillFailed = new Set<string>()
    for (const { file } of toDownload) {
      try {
        await downloadFile(token, file)
      } catch (err) {
        lib.flagAuthError(err)
        stillFailed.add(file.id)
      }
      await sleep(300)
    }
    setSelected(stillFailed)
    setDownloadError(
      stillFailed.size > 0
        ? `${stillFailed.size} of ${toDownload.length} download${toDownload.length === 1 ? '' : 's'} failed — still selected, try again.`
        : null,
    )
    setDownloading(false)
  }

  async function sendToKobo(file: DriveFile, device: KoboDevice) {
    if (!token) return
    setKoboError(null)
    setKoboMessage(null)
    try {
      await copyFileToFolder(token, file, device.folderId)
      setSentMap(markSent(device.folderId, [file.id]))
      setKoboMessage(`Sent "${file.name}" to ${device.label}.`)
    } catch (err) {
      lib.flagAuthError(err)
      setKoboError(err instanceof Error ? err.message : `Failed to send to ${device.label}.`)
    }
  }

  async function handleSendSelectedToKobo(device: KoboDevice) {
    if (!token) return
    setSendingToKobo(true)
    setKoboError(null)
    setKoboMessage(null)
    const toSend = allRows.filter((r) => selected.has(r.id))
    const sentIds: string[] = []
    const stillFailed = new Set<string>()
    for (const { file } of toSend) {
      try {
        await copyFileToFolder(token, file, device.folderId)
        sentIds.push(file.id)
      } catch (err) {
        lib.flagAuthError(err)
        stillFailed.add(file.id)
      }
    }
    if (sentIds.length > 0) setSentMap(markSent(device.folderId, sentIds))
    setSelected(stillFailed)
    setKoboMessage(`Sent ${sentIds.length} book${sentIds.length === 1 ? '' : 's'} to ${device.label}.`)
    setKoboError(
      stillFailed.size > 0
        ? `${stillFailed.size} of ${toSend.length} failed — still selected, try again.`
        : null,
    )
    setSendingToKobo(false)
  }

  if (showSetup) return <SetupChecklist onBack={() => setShowSetup(false)} />

  if (!settings || editingSettings) {
    return (
      <>
        <SettingsForm
          initial={loadPartialSettings()}
          onSave={(s) => {
            const resyncNeeded =
              settings?.googleClientId !== s.googleClientId ||
              settings?.libraryFolderId !== s.libraryFolderId
            saveSettings(s)
            setSettings(s)
            setEditingSettings(false)
            if (resyncNeeded) lib.reset()
          }}
          onCancel={settings ? () => setEditingSettings(false) : undefined}
        />
        <p className="mx-auto max-w-md p-6 pt-0 text-center">
          <button className="text-xs text-neutral-400 underline" onClick={() => setShowSetup(true)}>
            Lost your hard drive? Recovery checklist
          </button>
        </p>
      </>
    )
  }

  if (!token) {
    return (
      <div className="mx-auto max-w-sm px-6 pt-24 pb-12 text-center">
        <img src={`${import.meta.env.BASE_URL}favicon.svg`} alt="" className="mx-auto h-12 w-12" />
        <h1 className="mt-5 text-2xl font-semibold tracking-tight">BookBrain Library</h1>
        <p className="mt-2 text-sm text-neutral-500">Sign in with Google to browse your library.</p>
        <button
          className="btn btn-primary mx-auto mt-6 px-4 py-2 text-sm"
          disabled={lib.signingIn}
          onClick={lib.signIn}
        >
          {lib.signingIn ? 'Signing in…' : 'Sign in with Google'}
        </button>
        {lib.authError && <p className="mt-3 text-sm text-red-600">{lib.authError}</p>}
        <p className="mt-6 text-xs leading-relaxed text-neutral-400">
          {readOnly
            ? 'Signing in grants this page read-only access to your Google Drive so it can list and download the shared library. Nothing is saved: closing or reloading this page signs you out.'
            : 'Google only lets this page copy your existing library files with full Drive access, not a narrower "just this folder" permission — signing in grants access to your whole Drive, not only the library. Nothing is saved: closing or reloading this page signs you out.'}
        </p>
        <button
          className="mt-8 text-xs text-neutral-400 underline underline-offset-2 hover:text-neutral-600 dark:hover:text-neutral-300"
          onClick={() => setShowSetup(true)}
        >
          Lost your hard drive? Recovery checklist
        </button>
      </div>
    )
  }

  if (showDevices) {
    return (
      <DeviceLibrary
        token={token}
        devices={koboDevices}
        onBack={() => setShowDevices(false)}
        onRemoved={handleDeviceRemoval}
        onReconcile={handleDeviceReconcile}
      />
    )
  }

  const filterChips: { key: FilterKey; label: string }[] = [
    { key: 'all', label: 'All' },
    ...koboDevices.flatMap((d) => [
      { key: `off:${d.folderId}` as FilterKey, label: `Not on ${d.label}` },
      { key: `on:${d.folderId}` as FilterKey, label: `On ${d.label}` },
    ]),
    ...(koboDevices.length > 1 ? [{ key: 'unsent' as FilterKey, label: 'On no device' }] : []),
    { key: 'noseries', label: 'No series' },
  ]

  const koboStatus = koboError || koboMessage
  const emptyMessage =
    (files?.length ?? 0) === 0 ? 'No books found in this folder.' : 'Nothing matches.'

  return (
    <div className="mx-auto max-w-2xl px-4 pb-10 sm:px-6">
      <LibraryHeader
        busy={lib.syncing || lib.loading}
        hasKobo={hasKobo}
        readOnly={readOnly}
        onRefresh={lib.refresh}
        onRebuild={lib.rebuild}
        onShowDevices={() => setShowDevices(true)}
        onShare={handleShare}
        onCopyLink={handleCopyLink}
        onEditSettings={() => setEditingSettings(true)}
        onShowSetup={() => setShowSetup(true)}
        onForget={handleForget}
      />

      {lib.sessionExpired && (
        <div className="mb-2 flex items-center gap-3 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:border-amber-800/60 dark:bg-amber-950/30 dark:text-amber-300">
          <span className="flex-1">Your Google session expired.</span>
          <button className="btn btn-primary btn-xs" onClick={lib.signIn}>
            Reconnect
          </button>
        </div>
      )}

      <div className="sticky top-0 z-20 bg-neutral-50/95 py-2 backdrop-blur dark:bg-neutral-950/95">
        {selected.size > 0 && (
          <div className="mb-2 flex flex-wrap items-center gap-2 rounded-lg border border-neutral-200 bg-white px-3 py-2 text-sm shadow-sm dark:border-neutral-800 dark:bg-neutral-900">
            <span className="font-medium">{selected.size} selected</span>
            <button className="btn btn-neutral" onClick={() => setSelected(new Set())}>
              Clear
            </button>
            <span className="mx-1 hidden h-4 w-px bg-neutral-200 sm:block dark:bg-neutral-700" />
            <button className="btn btn-primary" disabled={downloading} onClick={handleDownloadSelected}>
              {downloading ? 'Downloading…' : `Download ${selected.size}`}
            </button>
            {koboDevices.map((device) => (
              <button
                key={device.folderId}
                className="btn btn-neutral"
                disabled={sendingToKobo}
                onClick={() => handleSendSelectedToKobo(device)}
              >
                {sendingToKobo ? 'Sending…' : `Send ${selected.size} to ${device.label}`}
              </button>
            ))}
            {downloadError && <span className="text-xs text-red-600">{downloadError}</span>}
            {hasKobo && koboError && <span className="text-xs text-red-600">{koboError}</span>}
            {hasKobo && !koboError && koboMessage && (
              <span className="text-xs text-neutral-500">{koboMessage}</span>
            )}
          </div>
        )}
        <div className="flex gap-2">
          <input
            className="field min-w-0 flex-1"
            placeholder="Search title, author, or series…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <select
            className="field shrink-0"
            value={sort}
            onChange={(e) => setSort(e.target.value as SortKey)}
            aria-label="Sort books"
          >
            {(Object.keys(SORT_LABELS) as SortKey[]).map((key) => (
              <option key={key} value={key}>
                {SORT_LABELS[key]}
              </option>
            ))}
          </select>
        </div>
        {filterChips.length > 2 && (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {filterChips.map((chip) => (
              <button
                key={chip.key}
                onClick={() => setFilter(chip.key)}
                className={`rounded-full border px-2.5 py-1 text-xs font-medium transition-colors ${
                  filter === chip.key
                    ? 'border-brand-600 bg-brand-600 text-white'
                    : 'border-neutral-300 bg-white text-neutral-600 hover:bg-neutral-100 dark:border-neutral-700 dark:bg-neutral-900 dark:text-neutral-300 dark:hover:bg-neutral-800'
                }`}
              >
                {chip.label}
              </button>
            ))}
          </div>
        )}
        {(lib.syncMessage || shareStatus) && !lib.syncing && !lib.loading && (
          <p className="mt-1.5 truncate text-xs text-neutral-400">
            {shareStatus ?? lib.syncMessage}
          </p>
        )}
      </div>

      {lib.loading && (
        <p className="mt-6 text-sm text-neutral-500">
          Building your library for the first time — this may take a moment…
        </p>
      )}
      {lib.loadError && <p className="mt-6 text-sm text-red-600">{lib.loadError}</p>}

      {selected.size === 0 && hasKobo && koboStatus && (
        <p className={`mt-3 text-xs ${koboError ? 'text-red-600' : 'text-neutral-500'}`}>
          {koboStatus}
        </p>
      )}

      {!lib.loading && files !== null && (
        <BookList
          rows={rows}
          allRows={allRows}
          totalCount={files.length}
          sort={sort}
          token={token}
          selected={selected}
          expandedId={expandedId}
          sentMap={sentMap}
          koboDevices={koboDevices}
          emptyMessage={emptyMessage}
          onToggleSelect={toggleSelected}
          onSelectMany={selectMany}
          onExpand={(id) => setExpandedId((cur) => (cur === id ? null : id))}
          onSend={sendToKobo}
          onDownload={(file) =>
            downloadFile(token, file).catch((err) => {
              lib.flagAuthError(err)
              setDownloadError(err.message)
            })
          }
        />
      )}
    </div>
  )
}
