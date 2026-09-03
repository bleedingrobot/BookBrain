import { useMemo, useState } from 'react'
import { DeviceLibrary } from './components/DeviceLibrary'
import { SettingsForm } from './components/SettingsForm'
import { SetupChecklist } from './components/SetupChecklist'
import { buildRows, groupHeading, matchesRow, SORT_LABELS, SORTS, type SortKey } from './lib/books'
import { copyFileToFolder, downloadFile, type DriveFile } from './lib/drive'
import { requestAccessToken } from './lib/googleAuth'
import {
  clearCachedIndex,
  fetchLibraryIndex,
  loadCachedIndex,
  type LibraryIndex,
} from './lib/libraryIndex'
import { clearLibraryCache, loadCachedFiles, syncLibrary } from './lib/librarySync'
import {
  clearSentTracker,
  getSentMap,
  markSent,
  unmarkSent,
} from './lib/sentTracker'
import {
  clearSettings,
  loadPartialSettings,
  loadSettings,
  saveSettings,
  type KoboDevice,
  type ViewerSettings,
} from './lib/settings'

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

// A share link (?clientId=...&folderId=...) hands a guest a ready-to-use,
// view/download-only config with no Kobo access — they never see the
// settings form at all. Consumed once on load, then scrubbed from the
// address bar so the values don't linger in browser history.
function consumeSharedSettings(): ViewerSettings | null {
  const params = new URLSearchParams(window.location.search)
  const googleClientId = params.get('clientId')
  const libraryFolderId = params.get('folderId')
  if (!googleClientId || !libraryFolderId) return null

  const shared: ViewerSettings = { googleClientId, libraryFolderId }
  saveSettings(shared)
  window.history.replaceState({}, '', window.location.pathname)
  return shared
}

export default function App() {
  const [showSetup, setShowSetup] = useState(false)
  const [showDevices, setShowDevices] = useState(false)
  const [editingSettings, setEditingSettings] = useState(false)
  const [settings, setSettings] = useState<ViewerSettings | null>(() => consumeSharedSettings() ?? loadSettings())
  const [token, setToken] = useState<string | null>(null)
  const [authError, setAuthError] = useState<string | null>(null)
  const [signingIn, setSigningIn] = useState(false)
  const [shareStatus, setShareStatus] = useState<string | null>(null)

  const [files, setFiles] = useState<DriveFile[] | null>(null)
  const [index, setIndex] = useState<LibraryIndex>({})
  const [loadError, setLoadError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false) // first-ever build, nothing cached to show meanwhile
  const [syncing, setSyncing] = useState(false) // background refresh, cached list already shown
  const [syncMessage, setSyncMessage] = useState<string | null>(null)

  const [query, setQuery] = useState('')
  const [sort, setSort] = useState<SortKey>('title')
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [downloading, setDownloading] = useState(false)
  const [downloadError, setDownloadError] = useState<string | null>(null)
  const [sendingToKobo, setSendingToKobo] = useState(false)
  const [koboError, setKoboError] = useState<string | null>(null)
  const [koboMessage, setKoboMessage] = useState<string | null>(null)
  const [sentMap, setSentMap] = useState(getSentMap)

  const allRows = useMemo(() => buildRows(files ?? [], index), [files, index])
  const rows = useMemo(() => {
    const filtered = allRows.filter((row) => matchesRow(row, query))
    filtered.sort(SORTS[sort])
    return filtered
  }, [allRows, query, sort])

  // Row ids per visible group heading, for the heading's select-all box.
  // Only meaningful on the name sorts, where headings actually render.
  const groupIndex = useMemo(() => {
    const map = new Map<string, string[]>()
    if (sort === 'author' || sort === 'series') {
      for (const row of rows) {
        const heading = groupHeading(row, sort)
        if (heading == null) continue
        const ids = map.get(heading)
        if (ids) ids.push(row.id)
        else map.set(heading, [row.id])
      }
    }
    return map
  }, [rows, sort])

  const koboDevices = settings?.koboDevices ?? []
  const hasKobo = koboDevices.length > 0

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

  function buildShareMessage(): { link: string; message: string } | null {
    if (!settings) return null
    const link = `${window.location.origin}${window.location.pathname}?clientId=${encodeURIComponent(settings.googleClientId)}&folderId=${encodeURIComponent(settings.libraryFolderId)}`
    const message = `You're invited to browse and download books from the BookBrain library. Open this link and sign in with your Google account:\n${link}`
    return { link, message }
  }

  async function handleShare() {
    const share = buildShareMessage()
    if (!share) return

    if (navigator.share) {
      try {
        await navigator.share({ title: 'BookBrain Library', text: share.message })
        return
      } catch {
        return // user cancelled the share sheet — not an error
      }
    }

    await handleCopyLink()
  }

  async function handleCopyLink() {
    const share = buildShareMessage()
    if (!share) return
    try {
      await navigator.clipboard.writeText(share.message)
      setShareStatus('Copied! Paste it into a message to send.')
    } catch {
      setShareStatus(`Copy failed — here's the link: ${share.link}`)
    }
    setTimeout(() => setShareStatus(null), 5000)
  }

  if (showSetup) {
    return <SetupChecklist onBack={() => setShowSetup(false)} />
  }

  if (!settings || editingSettings) {
    return (
      <>
        <SettingsForm
          initial={loadPartialSettings()}
          onSave={(s) => {
            // Only the client ID / library folder actually invalidate the
            // current sign-in and cached file list — changing just the Kobo
            // devices (or re-saving unchanged values to back out of
            // editing) shouldn't force a fresh sign-in + rebuild.
            const resyncNeeded =
              settings?.googleClientId !== s.googleClientId || settings?.libraryFolderId !== s.libraryFolderId
            saveSettings(s)
            setSettings(s)
            setEditingSettings(false)
            if (resyncNeeded) {
              setToken(null)
              setFiles(null)
            }
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

  async function runSync(newToken: string) {
    setLoadError(null)
    try {
      const { cache, rebuilt } = await syncLibrary(newToken, settings!.libraryFolderId)
      setFiles(cache.files)
      setSyncMessage(rebuilt ? `Library built — ${cache.files.length} books.` : 'Synced.')
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : 'Failed to load your library.')
    }
    // Independent of the file tree — its own failures fall back to the last
    // cached copy inside fetchLibraryIndex, so a missing/broken sidecar
    // never blocks the listing.
    setIndex(await fetchLibraryIndex(newToken, settings!.libraryFolderId))
  }

  async function handleSignIn() {
    setAuthError(null)
    setSigningIn(true)
    requestAccessToken(
      settings!.googleClientId,
      async (newToken) => {
        setSigningIn(false)
        setToken(newToken)

        const cached = loadCachedFiles(settings!.libraryFolderId)
        if (cached) {
          setFiles(cached)
          setIndex(loadCachedIndex(settings!.libraryFolderId)) // show metadata immediately; runSync refreshes it
          setSyncing(true)
          await runSync(newToken)
          setSyncing(false)
        } else {
          setLoading(true)
          await runSync(newToken)
          setLoading(false)
        }
      },
      (message) => {
        setSigningIn(false)
        setAuthError(message)
      },
    )
  }

  async function handleRefresh() {
    if (!token) return
    setSyncing(true)
    await runSync(token)
    setSyncing(false)
  }

  async function handleRebuild() {
    if (!token) return
    clearLibraryCache()
    clearCachedIndex()
    setLoading(true)
    await runSync(token)
    setLoading(false)
  }

  // Signing in never persists the access token (kept only in React state —
  // see `token` above), so a page reload already signs a visitor out. What
  // *does* persist to localStorage indefinitely is the saved client
  // ID/folder IDs and the cached library listing (titles, filenames, Drive
  // IDs) — on a shared/public device that's readable by the next person who
  // opens dev tools. This is the only way to clear it short of the browser's
  // own "clear site data".
  function handleForgetDevice() {
    clearSettings()
    clearLibraryCache()
    clearCachedIndex()
    clearSentTracker()
    setToken(null)
    setFiles(null)
    setIndex({})
    setSentMap({})
    setSettings(null)
  }

  function toggleSelected(id: string) {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  // A book removed from a device's folder is no longer "on" that device —
  // match it back to the library row by filename and drop the tick.
  function handleDeviceRemoval(folderId: string, filename: string) {
    const match = allRows.find((r) => r.filename === filename)
    if (match) setSentMap(unmarkSent(folderId, [match.id]))
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
      } catch {
        stillFailed.add(file.id) // one bad download shouldn't skip the rest of the selection
      }
      await sleep(300) // browsers throttle/block rapid-fire simultaneous downloads
    }
    setSelected(stillFailed) // leave failures selected so retrying is just clicking Download again
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
      } catch {
        stillFailed.add(file.id) // one failure shouldn't abort the rest of the batch
      }
    }
    if (sentIds.length > 0) setSentMap(markSent(device.folderId, sentIds))
    setSelected(stillFailed) // leave failures selected so retrying is just clicking Send again
    setKoboMessage(
      `Sent ${sentIds.length} book${sentIds.length === 1 ? '' : 's'} to ${device.label}.`,
    )
    setKoboError(
      stillFailed.size > 0
        ? `${stillFailed.size} of ${toSend.length} failed — still selected, try again.`
        : null,
    )
    setSendingToKobo(false)
  }

  if (!token) {
    return (
      <div className="mx-auto mt-16 max-w-md p-6 text-center">
        <h1 className="text-xl font-semibold">BookBrain Library</h1>
        <p className="mt-2 text-sm text-neutral-500">Sign in with Google to browse your library.</p>
        <button
          className="mt-6 rounded bg-neutral-900 px-4 py-2 text-sm text-white disabled:opacity-50 dark:bg-neutral-100 dark:text-neutral-900"
          disabled={signingIn}
          onClick={handleSignIn}
        >
          {signingIn ? 'Signing in…' : 'Sign in with Google'}
        </button>
        {authError && <p className="mt-3 text-sm text-red-600">{authError}</p>}
        <p className="mt-4 text-xs text-neutral-400">
          Google only lets this page copy your existing library files with full Drive access, not a
          narrower "just this folder" permission — signing in grants access to your whole Drive, not
          only the shared library. Nothing is saved: closing or reloading this page signs you out.
        </p>
        <button
          className="mt-8 block w-full text-xs text-neutral-400 underline"
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
      />
    )
  }

  return (
    <div className="mx-auto max-w-2xl p-6">
      <div className="flex items-center justify-between gap-4">
        <h1 className="text-xl font-semibold">BookBrain Library</h1>
        <div className="flex items-center gap-3 text-xs text-neutral-400">
          <button
            className="underline disabled:opacity-50"
            disabled={syncing || loading}
            onClick={handleRefresh}
          >
            {syncing ? 'Syncing…' : 'Refresh'}
          </button>
          <button
            className="underline disabled:opacity-50"
            disabled={syncing || loading}
            onClick={handleRebuild}
          >
            Rebuild
          </button>
          {hasKobo && (
            <button className="underline" onClick={() => setShowDevices(true)}>
              On devices
            </button>
          )}
          <button className="underline" onClick={handleShare}>
            Share
          </button>
          <button className="underline" onClick={handleCopyLink}>
            Copy link
          </button>
          <button className="underline" onClick={() => setEditingSettings(true)}>
            Change settings
          </button>
          <button className="underline" onClick={() => setShowSetup(true)}>
            Recovery checklist
          </button>
          <button className="underline" onClick={handleForgetDevice}>
            Forget this device
          </button>
        </div>
      </div>

      {syncMessage && !syncing && !loading && (
        <p className="mt-1 text-xs text-neutral-400">{syncMessage}</p>
      )}
      {shareStatus && <p className="mt-1 text-xs text-neutral-400">{shareStatus}</p>}

      <div className="mt-4 flex gap-2">
        <input
          className="min-w-0 flex-1 rounded border border-neutral-300 px-3 py-2 text-sm dark:border-neutral-700 dark:bg-neutral-900"
          placeholder="Search title, author, or series…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <select
          className="shrink-0 rounded border border-neutral-300 px-2 py-2 text-sm dark:border-neutral-700 dark:bg-neutral-900"
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

      {loading && (
        <p className="mt-6 text-sm text-neutral-500">
          Building your library for the first time — this may take a moment…
        </p>
      )}
      {loadError && <p className="mt-6 text-sm text-red-600">{loadError}</p>}

      {selected.size > 0 && (
        <div className="mt-4 flex flex-wrap items-center gap-3 rounded border border-neutral-200 p-3 text-sm dark:border-neutral-800">
          <span>{selected.size} selected</span>
          <button
            className="rounded bg-neutral-900 px-3 py-1.5 text-xs text-white disabled:opacity-50 dark:bg-neutral-100 dark:text-neutral-900"
            disabled={downloading}
            onClick={handleDownloadSelected}
          >
            {downloading ? 'Downloading…' : `Download ${selected.size} book${selected.size === 1 ? '' : 's'}`}
          </button>
          {koboDevices.map((device) => (
            <button
              key={device.folderId}
              className="rounded border border-neutral-300 px-3 py-1.5 text-xs disabled:opacity-50 dark:border-neutral-700"
              disabled={sendingToKobo}
              onClick={() => handleSendSelectedToKobo(device)}
            >
              {sendingToKobo
                ? 'Sending…'
                : `Send ${selected.size} book${selected.size === 1 ? '' : 's'} to ${device.label}`}
            </button>
          ))}
          <button
            className="text-xs text-neutral-400 underline"
            onClick={() => setSelected(new Set())}
          >
            Clear
          </button>
          {downloadError && <span className="text-xs text-red-600">{downloadError}</span>}
          {hasKobo && koboError && <span className="text-xs text-red-600">{koboError}</span>}
          {hasKobo && !koboError && koboMessage && (
            <span className="text-xs text-neutral-500">{koboMessage}</span>
          )}
        </div>
      )}
      {selected.size === 0 && hasKobo && (koboError || koboMessage) && (
        <p className={`mt-4 text-xs ${koboError ? 'text-red-600' : 'text-neutral-500'}`}>
          {koboError ?? koboMessage}
        </p>
      )}

      {!loading && files !== null && (
        <p className="mt-4 text-xs text-neutral-400">
          {rows.length === files.length
            ? `${files.length} book${files.length === 1 ? '' : 's'}`
            : `${rows.length} of ${files.length} books`}
        </p>
      )}

      <ul className="mt-1 divide-y divide-neutral-100 text-sm dark:divide-neutral-800">
        {rows.map((row, i) => {
          const heading = groupHeading(row, sort)
          const showHeading =
            heading != null && heading !== (i > 0 ? groupHeading(rows[i - 1], sort) : null)
          const groupIds = showHeading ? (groupIndex.get(heading) ?? []) : []
          const groupAllSelected = groupIds.length > 0 && groupIds.every((id) => selected.has(id))
          const expanded = expandedId === row.id
          const sentTo = koboDevices.filter((d) => sentMap[d.folderId]?.[row.id])
          const seriesPeers =
            expanded && row.series ? allRows.filter((r) => r.series === row.series) : []
          const authorPeers =
            expanded && row.author ? allRows.filter((r) => r.author === row.author) : []
          return (
            <li key={row.id} className="py-3">
              {showHeading && (
                <label className="-mt-1 mb-2 flex items-center gap-2 text-xs font-medium uppercase tracking-wide text-neutral-400">
                  <input
                    type="checkbox"
                    checked={groupAllSelected}
                    onChange={() => selectMany(groupIds, !groupAllSelected)}
                  />
                  {heading}
                  <span className="font-normal normal-case">({groupIds.length})</span>
                </label>
              )}
              <div className="flex items-center gap-3">
                <input
                  type="checkbox"
                  checked={selected.has(row.id)}
                  onChange={() => toggleSelected(row.id)}
                />
                <button
                  type="button"
                  className="min-w-0 flex-1 text-left"
                  onClick={() => setExpandedId((cur) => (cur === row.id ? null : row.id))}
                >
                  <div className="truncate font-medium">
                    {row.title}
                    {row.author && (
                      <span className="ml-2 font-normal text-neutral-500">by {row.author}</span>
                    )}
                  </div>
                  {row.series && (
                    <div className="truncate text-xs text-neutral-400">
                      {row.series}
                      {row.seriesNumber && ` #${row.seriesNumber}`}
                    </div>
                  )}
                  {sentTo.length > 0 && (
                    <div className="text-xs text-emerald-600 dark:text-emerald-400">
                      {sentTo.map((d) => `✓ ${d.label}`).join('   ')}
                    </div>
                  )}
                </button>
                <button
                  className="shrink-0 rounded border border-neutral-300 px-2 py-1 text-xs dark:border-neutral-700"
                  onClick={() =>
                    downloadFile(token, row.file).catch((err) => setDownloadError(err.message))
                  }
                >
                  Download
                </button>
                {koboDevices.map((device) => (
                  <button
                    key={device.folderId}
                    className="shrink-0 rounded border border-neutral-300 px-2 py-1 text-xs dark:border-neutral-700"
                    onClick={() => sendToKobo(row.file, device)}
                  >
                    {koboDevices.length === 1 ? 'Send to Kobo' : `→ ${device.label}`}
                  </button>
                ))}
              </div>
              {expanded && (
                <div className="mt-2 pl-7 text-xs text-neutral-500">
                  {row.description ? (
                    <p>{row.description}</p>
                  ) : (
                    <p className="italic text-neutral-400">No description on file.</p>
                  )}
                  {row.addedAt && (
                    <p className="mt-1 text-neutral-400">
                      Added {new Date(row.addedAt).toLocaleDateString()}
                    </p>
                  )}
                  {(seriesPeers.length > 1 || authorPeers.length > 1) && (
                    <div className="mt-2 flex flex-wrap gap-2">
                      {seriesPeers.length > 1 && (
                        <button
                          className="rounded border border-neutral-300 px-2 py-1 dark:border-neutral-700"
                          onClick={() => selectMany(seriesPeers.map((r) => r.id), true)}
                        >
                          Select all {seriesPeers.length} in {row.series}
                        </button>
                      )}
                      {authorPeers.length > 1 && (
                        <button
                          className="rounded border border-neutral-300 px-2 py-1 dark:border-neutral-700"
                          onClick={() => selectMany(authorPeers.map((r) => r.id), true)}
                        >
                          Select all {authorPeers.length} by {row.author}
                        </button>
                      )}
                    </div>
                  )}
                </div>
              )}
            </li>
          )
        })}
        {!loading && files !== null && rows.length === 0 && (
          <li className="py-4 text-neutral-400">
            {files.length === 0
              ? 'No books found in this folder.'
              : 'No books match your search.'}
          </li>
        )}
      </ul>
    </div>
  )
}
