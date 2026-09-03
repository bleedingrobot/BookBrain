import { Fragment, useMemo, useState } from 'react'
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
  const [menuOpen, setMenuOpen] = useState(false)
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
      <div className="mx-auto max-w-sm px-6 pt-24 pb-12 text-center">
        <img
          src={`${import.meta.env.BASE_URL}favicon.svg`}
          alt=""
          className="mx-auto h-12 w-12"
        />
        <h1 className="mt-5 text-2xl font-semibold tracking-tight">BookBrain Library</h1>
        <p className="mt-2 text-sm text-neutral-500">
          Sign in with Google to browse your library.
        </p>
        <button
          className="btn btn-primary mx-auto mt-6 px-4 py-2 text-sm"
          disabled={signingIn}
          onClick={handleSignIn}
        >
          {signingIn ? 'Signing in…' : 'Sign in with Google'}
        </button>
        {authError && <p className="mt-3 text-sm text-red-600">{authError}</p>}
        <p className="mt-6 text-xs leading-relaxed text-neutral-400">
          Google only lets this page copy your existing library files with full Drive access, not a
          narrower "just this folder" permission — signing in grants access to your whole Drive, not
          only the shared library. Nothing is saved: closing or reloading this page signs you out.
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
      />
    )
  }

  return (
    <div className="mx-auto max-w-2xl px-4 pb-24 sm:px-6">
      <header className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 py-4">
        <div className="flex items-center gap-2.5">
          <img src={`${import.meta.env.BASE_URL}favicon.svg`} alt="" className="h-6 w-6" />
          <h1 className="text-lg font-semibold tracking-tight">BookBrain Library</h1>
        </div>
        <div className="flex items-center gap-1">
          <button
            className="btn btn-ghost"
            disabled={syncing || loading}
            onClick={handleRefresh}
          >
            {syncing ? 'Syncing…' : 'Refresh'}
          </button>
          {hasKobo && (
            <button className="btn btn-ghost" onClick={() => setShowDevices(true)}>
              On devices
            </button>
          )}
          <div className="relative">
            <button
              className="btn btn-ghost px-2"
              aria-label="More actions"
              aria-expanded={menuOpen}
              onClick={() => setMenuOpen((o) => !o)}
            >
              <span className="text-base leading-none">⋯</span>
            </button>
            {menuOpen && (
              <>
                <button
                  className="fixed inset-0 z-10 cursor-default"
                  aria-hidden
                  tabIndex={-1}
                  onClick={() => setMenuOpen(false)}
                />
                <div className="card absolute right-0 z-20 mt-1 w-44 overflow-hidden p-1 shadow-lg">
                  {[
                    { label: 'Rebuild library', fn: handleRebuild, disabled: syncing || loading },
                    { label: 'Share…', fn: handleShare },
                    { label: 'Copy link', fn: handleCopyLink },
                    { label: 'Change settings', fn: () => setEditingSettings(true) },
                    { label: 'Recovery checklist', fn: () => setShowSetup(true) },
                  ].map((item) => (
                    <button
                      key={item.label}
                      disabled={item.disabled}
                      className="block w-full rounded px-2.5 py-1.5 text-left text-xs text-neutral-700 hover:bg-neutral-100 disabled:opacity-50 dark:text-neutral-200 dark:hover:bg-neutral-800"
                      onClick={() => {
                        setMenuOpen(false)
                        item.fn()
                      }}
                    >
                      {item.label}
                    </button>
                  ))}
                  <div className="my-1 border-t border-neutral-200 dark:border-neutral-800" />
                  <button
                    className="block w-full rounded px-2.5 py-1.5 text-left text-xs text-red-600 hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-950/40"
                    onClick={() => {
                      setMenuOpen(false)
                      handleForgetDevice()
                    }}
                  >
                    Forget this device
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      </header>

      <div className="sticky top-0 z-[5] bg-neutral-50/95 py-2 backdrop-blur dark:bg-neutral-950/95">
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
        {(syncMessage || shareStatus) && !syncing && !loading && (
          <p className="mt-1.5 truncate text-xs text-neutral-400">{shareStatus ?? syncMessage}</p>
        )}
      </div>

      {loading && (
        <p className="mt-6 text-sm text-neutral-500">
          Building your library for the first time — this may take a moment…
        </p>
      )}
      {loadError && <p className="mt-6 text-sm text-red-600">{loadError}</p>}

      {selected.size === 0 && hasKobo && (koboError || koboMessage) && (
        <p className={`mt-3 text-xs ${koboError ? 'text-red-600' : 'text-neutral-500'}`}>
          {koboError ?? koboMessage}
        </p>
      )}

      {!loading && files !== null && (
        <p className="mt-4 text-xs font-medium text-neutral-400">
          {rows.length === files.length
            ? `${files.length} book${files.length === 1 ? '' : 's'}`
            : `${rows.length} of ${files.length} books`}
        </p>
      )}

      {selected.size > 0 && (
        <div className="fixed inset-x-0 bottom-0 z-30 border-t border-neutral-200 bg-white/95 backdrop-blur dark:border-neutral-800 dark:bg-neutral-900/95">
          <div className="mx-auto flex max-w-2xl flex-wrap items-center gap-2 px-4 py-3 text-sm sm:px-6">
            <span className="font-medium">{selected.size} selected</span>
            <button
              className="btn btn-neutral"
              onClick={() => setSelected(new Set())}
            >
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
        </div>
      )}

      <ul className="mt-1 text-sm">
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
            <Fragment key={row.id}>
              {showHeading && (
                <li className="mt-3 flex items-center gap-3 border-b border-neutral-200 pb-1.5 first:mt-0 dark:border-neutral-800">
                  <input
                    type="checkbox"
                    className="accent-brand-600"
                    checked={groupAllSelected}
                    aria-label={`Select all in ${heading}`}
                    onChange={() => selectMany(groupIds, !groupAllSelected)}
                  />
                  <span className="text-xs font-semibold tracking-wide text-neutral-500 uppercase dark:text-neutral-400">
                    {heading}
                  </span>
                  <span className="text-[11px] text-neutral-400">{groupIds.length}</span>
                </li>
              )}
              <li
                className={`flex flex-wrap items-start gap-x-3 gap-y-2 py-2.5 ${
                  expanded ? '' : 'border-b border-neutral-100 dark:border-neutral-800/60'
                }`}
              >
                <input
                  type="checkbox"
                  className="mt-1 accent-brand-600"
                  checked={selected.has(row.id)}
                  onChange={() => toggleSelected(row.id)}
                />
                <button
                  type="button"
                  className="min-w-0 flex-1 text-left"
                  onClick={() => setExpandedId((cur) => (cur === row.id ? null : row.id))}
                >
                  <div className="truncate font-medium text-neutral-900 dark:text-neutral-100">
                    {row.title}
                    {row.author && (
                      <span className="ml-2 font-normal text-neutral-500">{row.author}</span>
                    )}
                  </div>
                  {(row.series || sentTo.length > 0) && (
                    <div className="mt-1 flex flex-wrap items-center gap-1.5">
                      {row.series && (
                        <span className="badge bg-brand-50 text-brand-700 dark:bg-brand-950/60 dark:text-brand-300">
                          {row.series}
                          {row.seriesNumber && ` #${row.seriesNumber}`}
                        </span>
                      )}
                      {sentTo.map((d) => (
                        <span
                          key={d.folderId}
                          className="badge bg-emerald-50 text-emerald-700 dark:bg-emerald-950/50 dark:text-emerald-400"
                        >
                          ✓ {d.label}
                        </span>
                      ))}
                    </div>
                  )}
                </button>
                <div className="flex w-full shrink-0 flex-wrap items-center gap-1 pl-7 sm:w-auto sm:pl-0">
                  <button
                    className="btn btn-ghost btn-xs"
                    onClick={() =>
                      downloadFile(token, row.file).catch((err) => setDownloadError(err.message))
                    }
                  >
                    Download
                  </button>
                  {koboDevices.map((device) => (
                    <button
                      key={device.folderId}
                      className="btn btn-neutral btn-xs"
                      onClick={() => sendToKobo(row.file, device)}
                    >
                      {koboDevices.length === 1 ? 'Send to Kobo' : `→ ${device.label}`}
                    </button>
                  ))}
                </div>
              </li>
              {expanded && (
                <li className="mb-1 rounded-lg bg-neutral-100/70 p-3 dark:bg-neutral-800/30">
                  <div className="text-xs leading-relaxed text-neutral-600 sm:pl-7 dark:text-neutral-400">
                    {row.description ? (
                      <p>{row.description}</p>
                    ) : (
                      <p className="text-neutral-400 italic">No description on file.</p>
                    )}
                    {row.addedAt && (
                      <p className="mt-1.5 text-neutral-400">
                        Added {new Date(row.addedAt).toLocaleDateString()}
                      </p>
                    )}
                    {(seriesPeers.length > 1 || authorPeers.length > 1) && (
                      <div className="mt-2.5 flex flex-wrap gap-1.5">
                        {seriesPeers.length > 1 && (
                          <button
                            className="btn btn-neutral btn-xs"
                            onClick={() => selectMany(seriesPeers.map((r) => r.id), true)}
                          >
                            Select all {seriesPeers.length} in {row.series}
                          </button>
                        )}
                        {authorPeers.length > 1 && (
                          <button
                            className="btn btn-neutral btn-xs"
                            onClick={() => selectMany(authorPeers.map((r) => r.id), true)}
                          >
                            Select all {authorPeers.length} by {row.author}
                          </button>
                        )}
                      </div>
                    )}
                  </div>
                </li>
              )}
            </Fragment>
          )
        })}
        {!loading && files !== null && rows.length === 0 && (
          <li className="py-10 text-center text-sm text-neutral-400">
            {files.length === 0
              ? 'No books found in this folder.'
              : 'No books match your search.'}
          </li>
        )}
      </ul>
    </div>
  )
}
