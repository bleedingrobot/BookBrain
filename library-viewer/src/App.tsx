import { Fragment, useEffect, useMemo, useRef, useState } from 'react'
import { Cover } from './components/Cover'
import { DeviceLibrary } from './components/DeviceLibrary'
import { SettingsForm } from './components/SettingsForm'
import { SetupChecklist } from './components/SetupChecklist'
import {
  buildRows,
  groupHeading,
  matchesFilter,
  matchesRow,
  SORT_LABELS,
  SORTS,
  type FilterKey,
  type SortKey,
} from './lib/books'
import { clearCoverCache, loadCoverManifest } from './lib/covers'
import { copyFileToFolder, downloadFile, type DriveFile } from './lib/drive'
import { requestAccessToken, SCOPE_FULL, SCOPE_READONLY } from './lib/googleAuth'
import {
  clearCachedIndex,
  EMPTY_INDEX,
  fetchLibraryIndex,
  loadCachedIndex,
  type LibraryIndex,
} from './lib/libraryIndex'
import { clearLibraryCache, loadCachedFiles, syncLibrary } from './lib/librarySync'
import { clearSentTracker, getSentMap, markSent, unmarkSent } from './lib/sentTracker'
import {
  clearSettings,
  loadPartialSettings,
  loadSettings,
  saveSettings,
  type KoboDevice,
  type ViewerSettings,
} from './lib/settings'

const PAGE_SIZE = 120 // rows rendered before the "load more" sentinel kicks in

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

function isAuthError(err: unknown): boolean {
  return (
    err instanceof Error &&
    /sign-in expired|invalid credentials|invalid authentication|\b401\b/i.test(err.message)
  )
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
  const [menuOpen, setMenuOpen] = useState(false)
  const [settings, setSettings] = useState<ViewerSettings | null>(
    () => consumeSharedSettings() ?? loadSettings(),
  )
  const [token, setToken] = useState<string | null>(null)
  const [tokenExpiresAt, setTokenExpiresAt] = useState(0)
  const [sessionExpired, setSessionExpired] = useState(false)
  const [authError, setAuthError] = useState<string | null>(null)
  const [signingIn, setSigningIn] = useState(false)
  const [shareStatus, setShareStatus] = useState<string | null>(null)

  const [files, setFiles] = useState<DriveFile[] | null>(null)
  const [index, setIndex] = useState<LibraryIndex>(EMPTY_INDEX)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [syncing, setSyncing] = useState(false)
  const [syncMessage, setSyncMessage] = useState<string | null>(null)

  const [query, setQuery] = useState('')
  const [sort, setSort] = useState<SortKey>('title')
  const [filter, setFilter] = useState<FilterKey>('all')
  const [visibleCount, setVisibleCount] = useState(PAGE_SIZE)
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [downloading, setDownloading] = useState(false)
  const [downloadError, setDownloadError] = useState<string | null>(null)
  const [sendingToKobo, setSendingToKobo] = useState(false)
  const [koboError, setKoboError] = useState<string | null>(null)
  const [koboMessage, setKoboMessage] = useState<string | null>(null)
  const [sentMap, setSentMap] = useState(getSentMap)

  const readOnly = settings?.readOnly ?? false
  const scope = readOnly ? SCOPE_READONLY : SCOPE_FULL
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

  const visibleRows = useMemo(() => rows.slice(0, visibleCount), [rows, visibleCount])

  // Row ids per visible group heading, for the heading's select-all box.
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

  // Reset the render window whenever the result set changes.
  useEffect(() => setVisibleCount(PAGE_SIZE), [query, sort, filter])

  const sentinelRef = useRef<HTMLLIElement>(null)
  useEffect(() => {
    const el = sentinelRef.current
    if (!el) return
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) setVisibleCount((c) => c + PAGE_SIZE)
      },
      { rootMargin: '600px' },
    )
    observer.observe(el)
    return () => observer.disconnect()
  }, [visibleRows.length])

  function applyToken(newToken: string, expiresInSeconds: number) {
    setToken(newToken)
    setTokenExpiresAt(Date.now() + expiresInSeconds * 1000)
    setSessionExpired(false)
  }

  // Silently renew the access token ~2 min before it lapses, so a long
  // session doesn't hit a wall of 401s mid-task.
  useEffect(() => {
    if (!token || !settings || !tokenExpiresAt) return
    const delay = Math.max(10_000, tokenExpiresAt - Date.now() - 120_000)
    const id = setTimeout(() => {
      requestAccessToken(
        settings.googleClientId,
        scope,
        (t, exp) => applyToken(t, exp),
        () => setSessionExpired(true),
        { silent: true },
      )
    }, delay)
    return () => clearTimeout(id)
  }, [token, tokenExpiresAt, settings, scope])

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
        return
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
            const resyncNeeded =
              settings?.googleClientId !== s.googleClientId ||
              settings?.libraryFolderId !== s.libraryFolderId
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
      if (isAuthError(err)) setSessionExpired(true)
      setLoadError(err instanceof Error ? err.message : 'Failed to load your library.')
    }
    const idx = await fetchLibraryIndex(newToken, settings!.libraryFolderId)
    setIndex(idx)
    await loadCoverManifest(newToken, idx.coversFolder)
  }

  function handleSignIn() {
    setAuthError(null)
    setSigningIn(true)
    requestAccessToken(
      settings!.googleClientId,
      scope,
      async (newToken, expiresIn) => {
        setSigningIn(false)
        applyToken(newToken, expiresIn)

        const cached = loadCachedFiles(settings!.libraryFolderId)
        if (cached) {
          setFiles(cached)
          setIndex(loadCachedIndex(settings!.libraryFolderId))
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
    clearCoverCache()
    setLoading(true)
    await runSync(token)
    setLoading(false)
  }

  function handleForgetDevice() {
    clearSettings()
    clearLibraryCache()
    clearCachedIndex()
    clearCoverCache()
    clearSentTracker()
    setToken(null)
    setFiles(null)
    setIndex(EMPTY_INDEX)
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

  function handleDeviceRemoval(folderId: string, filename: string) {
    const match = allRows.find((r) => r.filename === filename)
    if (match) setSentMap(unmarkSent(folderId, [match.id]))
  }

  // The device screen reports the real folder contents; make the local
  // "✓ on device" ticks match — add any book sitting in the folder, drop
  // any tick for a book that isn't there any more. Matched by filename
  // (the copy keeps the library file's name).
  function handleDeviceReconcile(folderId: string, filenames: string[]) {
    const present = new Set(filenames)
    const shouldBeSent = allRows.filter((r) => present.has(r.filename)).map((r) => r.id)
    const currentlySent = Object.keys(sentMap[folderId] ?? {})
    const staleIds = currentlySent.filter((id) => {
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
        if (isAuthError(err)) setSessionExpired(true)
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
      if (isAuthError(err)) setSessionExpired(true)
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
        if (isAuthError(err)) setSessionExpired(true)
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

  if (!token) {
    return (
      <div className="mx-auto max-w-sm px-6 pt-24 pb-12 text-center">
        <img
          src={`${import.meta.env.BASE_URL}favicon.svg`}
          alt=""
          className="mx-auto h-12 w-12"
        />
        <h1 className="mt-5 text-2xl font-semibold tracking-tight">BookBrain Library</h1>
        <p className="mt-2 text-sm text-neutral-500">Sign in with Google to browse your library.</p>
        <button
          className="btn btn-primary mx-auto mt-6 px-4 py-2 text-sm"
          disabled={signingIn}
          onClick={handleSignIn}
        >
          {signingIn ? 'Signing in…' : 'Sign in with Google'}
        </button>
        {authError && <p className="mt-3 text-sm text-red-600">{authError}</p>}
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

  return (
    <div className="mx-auto max-w-2xl px-4 pb-10 sm:px-6">
      <header className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 py-4">
        <div className="flex items-center gap-2.5">
          <img src={`${import.meta.env.BASE_URL}favicon.svg`} alt="" className="h-6 w-6" />
          <h1 className="text-lg font-semibold tracking-tight">BookBrain Library</h1>
        </div>
        <div className="flex items-center gap-1">
          <button className="btn btn-ghost" disabled={syncing || loading} onClick={handleRefresh}>
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
                    ...(readOnly
                      ? []
                      : [{ label: 'Rebuild library', fn: handleRebuild, disabled: syncing || loading }]),
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

      {sessionExpired && (
        <div className="mb-2 flex items-center gap-3 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:border-amber-800/60 dark:bg-amber-950/30 dark:text-amber-300">
          <span className="flex-1">Your Google session expired.</span>
          <button className="btn btn-primary btn-xs" onClick={handleSignIn}>
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

      <ul className="mt-1 text-sm">
        {visibleRows.map((row, i) => {
          const heading = groupHeading(row, sort)
          const showHeading =
            heading != null && heading !== (i > 0 ? groupHeading(visibleRows[i - 1], sort) : null)
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
                  className="flex min-w-0 flex-1 items-start gap-3 text-left"
                  onClick={() => setExpandedId((cur) => (cur === row.id ? null : row.id))}
                >
                  <Cover token={token} driveId={row.id} isbn={row.isbn} />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-medium text-neutral-900 dark:text-neutral-100">
                      {row.title}
                      {row.author && (
                        <span className="ml-2 font-normal text-neutral-500">{row.author}</span>
                      )}
                    </span>
                    {(row.series || sentTo.length > 0) && (
                      <span className="mt-1 flex flex-wrap items-center gap-1.5">
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
                      </span>
                    )}
                  </span>
                </button>
                <div className="flex w-full shrink-0 flex-wrap items-center gap-1 pl-7 sm:w-auto sm:pl-0">
                  <button
                    className="btn btn-ghost btn-xs"
                    onClick={() =>
                      downloadFile(token, row.file).catch((err) => {
                        if (isAuthError(err)) setSessionExpired(true)
                        setDownloadError(err.message)
                      })
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
        {rows.length > visibleRows.length && (
          <li ref={sentinelRef} className="py-6 text-center text-xs text-neutral-400">
            Loading more… ({visibleRows.length} of {rows.length})
          </li>
        )}
        {!loading && files !== null && rows.length === 0 && (
          <li className="py-10 text-center text-sm text-neutral-400">
            {files.length === 0 ? 'No books found in this folder.' : 'Nothing matches.'}
          </li>
        )}
      </ul>
    </div>
  )
}
