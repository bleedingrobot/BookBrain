import { useEffect, useMemo, useRef, useState } from 'react'
import { ActivityScreen } from './components/ActivityScreen'
import { BookList } from './components/BookList'
import { DeviceLibrary } from './components/DeviceLibrary'
import { LibraryHeader } from './components/LibraryHeader'
import { RecentMarquee } from './components/RecentMarquee'
import { SettingsForm } from './components/SettingsForm'
import { SetupChecklist } from './components/SetupChecklist'
import { WhoAmI } from './components/WhoAmI'
import { WishlistScreen } from './components/WishlistScreen'
import { logActivity } from './lib/activityLog'
import {
  buildRows,
  matchesFilter,
  matchesRow,
  sendKey,
  SORT_LABELS,
  SORTS,
  type FilterKey,
  type SendStatus,
  type SortKey,
} from './lib/books'
import { copyFileToFolder, downloadFile, type DriveFile } from './lib/drive'
import { pickRecentBooks } from './lib/marquee'
import { computeSeriesGaps, incompleteSeriesNames } from './lib/seriesGaps'
import { clearSentTracker, getSentMap, markSent, unmarkSent } from './lib/sentTracker'
import {
  clearSettings,
  loadPartialSettings,
  loadSettings,
  saveSettings,
  type KoboDevice,
  type ViewerSettings,
} from './lib/settings'
import { getViewerName, setViewerName } from './lib/viewerIdentity'
import { useLibrary } from './hooks/useLibrary'

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

// A share link (?clientId=...&folderId=...) points a device at a specific
// library with no setup form. Rarely needed now that the deployed build
// ships working defaults (see lib/config.ts) — kept for pointing a device
// at a different library/client. Consumed once on load, then scrubbed from
// the address bar so the values don't linger in browser history.
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
  const [viewerName, setViewerNameState] = useState(getViewerName)
  const [showSetup, setShowSetup] = useState(false)
  const [showDevices, setShowDevices] = useState(false)
  const [showWishlist, setShowWishlist] = useState(false)
  const [showActivity, setShowActivity] = useState(false)
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
  const [showAll, setShowAll] = useState(false)
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [pendingScrollId, setPendingScrollId] = useState<string | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [downloading, setDownloading] = useState(false)
  const [downloadError, setDownloadError] = useState<string | null>(null)
  const [sendingToKobo, setSendingToKobo] = useState(false)
  const [koboError, setKoboError] = useState<string | null>(null)
  const [koboMessage, setKoboMessage] = useState<string | null>(null)
  // Per-row, per-device send state (keyed by sendKey) — lets each "→ Tess"
  // button show its own "Sending…"/failed state immediately, so a slow or
  // failed send is obvious right at the button instead of only in the
  // page-level banner below, which is easy to miss (and easy to mistake
  // for "nothing happened" — the exact thing that led to someone clicking
  // the same button three times in a row).
  const [sendState, setSendState] = useState<Record<string, SendStatus>>({})
  const [sentMap, setSentMap] = useState(getSentMap)

  // Drive-synced (lib.remoteKoboDevices) once a sync has resolved it —
  // falls back to this browser's own local copy until then, or if the
  // library has no synced settings file at all yet.
  const koboDevices = lib.remoteKoboDevices ?? settings?.koboDevices ?? []
  const hasKobo = koboDevices.length > 0

  function logDownload(file: DriveFile) {
    if (!token || !settings || !viewerName) return
    void logActivity(token, settings.libraryFolderId, viewerName, 'download', file.name)
  }

  function logKoboSend(file: DriveFile, device: KoboDevice) {
    if (!token || !settings || !viewerName) return
    void logActivity(token, settings.libraryFolderId, viewerName, 'kobo-send', `${file.name} → ${device.label}`)
  }

  const allRows = useMemo(() => buildRows(files ?? [], index), [files, index])
  const recentBooks = useMemo(() => pickRecentBooks(allRows), [allRows])
  const seriesGaps = useMemo(() => computeSeriesGaps(allRows), [allRows])
  const incompleteSeries = useMemo(() => incompleteSeriesNames(seriesGaps), [seriesGaps])
  const rows = useMemo(() => {
    const out = allRows.filter(
      (row) => matchesRow(row, query) && matchesFilter(row, filter, sentMap, incompleteSeries),
    )
    out.sort(SORTS[sort])
    return out
  }, [allRows, query, sort, filter, sentMap, incompleteSeries])

  // Log a search a beat after typing settles, not on every keystroke — a
  // read+write to Drive per character would be both wasteful and racy.
  const lastLoggedQueryRef = useRef('')
  useEffect(() => {
    if (!token || !settings || !viewerName) return
    const trimmed = query.trim()
    if (trimmed.length < 2 || trimmed === lastLoggedQueryRef.current) return
    const folderId = settings.libraryFolderId
    const id = setTimeout(() => {
      lastLoggedQueryRef.current = trimmed
      void logActivity(token, folderId, viewerName, 'search', trimmed)
    }, 800)
    return () => clearTimeout(id)
  }, [query, token, viewerName, settings])

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

  function filterTo(value: string, by: SortKey) {
    setQuery(value)
    setSort(by)
    setFilter('all')
    window.scrollTo({ top: 0 })
  }

  // A cover in the "recently added" ticker was clicked — switch the list to
  // the recently-added view (where the book is near the top), open it, and
  // scroll it into view once it's rendered.
  function jumpToRecent(id: string) {
    setQuery('')
    setFilter('all')
    setSort('added')
    setShowAll(true)
    setExpandedId(id)
    setPendingScrollId(id)
  }

  useEffect(() => {
    if (!pendingScrollId) return
    const el = document.getElementById(`book-${pendingScrollId}`)
    if (!el) return
    el.scrollIntoView({ behavior: 'smooth', block: 'center' })
    setPendingScrollId(null)
  }, [pendingScrollId, rows])

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
        logDownload(file)
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
    const key = sendKey(file.id, device.folderId)
    if (sendState[key] === 'pending') return // already in flight — the disabled button should prevent this anyway
    setKoboError(null)
    setKoboMessage(null)
    setSendState((prev) => ({ ...prev, [key]: 'pending' }))
    try {
      await copyFileToFolder(token, file, device.folderId)
      logKoboSend(file, device)
      setSentMap(markSent(device.folderId, [file.id]))
      setKoboMessage(`Sent "${file.name}" to ${device.label}.`)
      setSendState((prev) => {
        const { [key]: _removed, ...rest } = prev
        return rest
      })
    } catch (err) {
      lib.flagAuthError(err)
      setKoboError(err instanceof Error ? err.message : `Failed to send to ${device.label}.`)
      setSendState((prev) => ({ ...prev, [key]: 'error' }))
      // Leave the button showing "Failed" for a few seconds rather than
      // instantly reverting to its normal label — long enough to register
      // as feedback, short enough that a genuine retry isn't stuck looking
      // like a stale error.
      setTimeout(() => {
        setSendState((prev) => {
          if (prev[key] !== 'error') return prev
          const { [key]: _removed, ...rest } = prev
          return rest
        })
      }, 4000)
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
        logKoboSend(file, device)
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

  if (!viewerName) {
    return (
      <WhoAmI
        onPick={(name) => {
          setViewerName(name)
          setViewerNameState(name)
        }}
      />
    )
  }

  if (showSetup) return <SetupChecklist onBack={() => setShowSetup(false)} />

  if (!settings || editingSettings) {
    return (
      <>
        <SettingsForm
          // Prefer whatever's synced from Drive for Kobo devices over this
          // browser's own (possibly empty, possibly stale) local copy, once
          // a sync has actually resolved one — otherwise re-opening
          // Settings on a second device would show blank rows even though
          // sending books already works. `lib.remoteKoboDevices` is null
          // (not [] ?? []) until that first sync completes, so this only
          // overrides once there's something real to show.
          initial={{
            ...loadPartialSettings(),
            ...(lib.remoteKoboDevices ? { koboDevices: lib.remoteKoboDevices } : {}),
          }}
          onSave={(s) => {
            const resyncNeeded =
              settings?.googleClientId !== s.googleClientId ||
              settings?.libraryFolderId !== s.libraryFolderId
            saveSettings(s)
            setSettings(s)
            setEditingSettings(false)
            if (resyncNeeded) lib.reset()
            // Already signed in (editing, not first-time setup) — push the
            // Kobo device list to Drive right away rather than waiting for
            // the next sync, so another device picks up the change sooner.
            else lib.saveKoboDevices(s.koboDevices ?? [])
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
          Sending books to a Kobo copies files between Drive folders, which Google only allows with
          full Drive access — so signing in grants this page access to your Drive, not just the
          library folder. Nothing is saved anywhere but Google: closing or reloading this page signs
          you out.
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

  if (showActivity) {
    return (
      <ActivityScreen
        token={token}
        libraryFolderId={settings.libraryFolderId}
        onBack={() => setShowActivity(false)}
      />
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

  if (showWishlist) {
    return (
      <WishlistScreen
        token={token}
        libraryFolderId={settings.libraryFolderId}
        rows={allRows}
        onBack={() => setShowWishlist(false)}
      />
    )
  }

  const filterChips: { key: FilterKey; label: string }[] = [
    { key: 'all', label: 'All' },
    ...koboDevices.map((d) => ({ key: `on:${d.folderId}` as FilterKey, label: `On ${d.label}` })),
    ...(incompleteSeries.size > 0 ? [{ key: 'gaps' as FilterKey, label: 'Missing books' }] : []),
  ]

  const koboStatus = koboError || koboMessage
  const emptyMessage =
    (files?.length ?? 0) === 0 ? 'No books found in this folder.' : 'Nothing matches.'

  // Don't dump the whole library on screen — wait for a search, a filter,
  // or an explicit "show everything".
  const browsing = query.trim() !== '' || filter !== 'all' || showAll

  return (
    <div className="mx-auto max-w-2xl px-4 pb-10 sm:px-6">
      <LibraryHeader
        busy={lib.syncing || lib.loading}
        hasKobo={hasKobo}
        onRefresh={lib.refresh}
        onRebuild={lib.rebuild}
        onShowDevices={() => setShowDevices(true)}
        onShowWishlist={() => setShowWishlist(true)}
        onShowActivity={() => setShowActivity(true)}
        onShare={handleShare}
        onCopyLink={handleCopyLink}
        onEditSettings={() => setEditingSettings(true)}
        onShowSetup={() => setShowSetup(true)}
        onForget={handleForget}
      />

      {!lib.loading && (
        <RecentMarquee books={recentBooks} token={token} onPick={jumpToRecent} />
      )}

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
        {filterChips.length > 1 && (
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

      {!lib.loading && files !== null && !browsing && (
        <div className="mt-10 text-center text-sm text-neutral-400">
          <p>
            {files.length.toLocaleString()} book{files.length === 1 ? '' : 's'} in your library.
            <br />
            Search, or pick a filter, to see them.
          </p>
          <button
            className="btn btn-neutral mt-4"
            onClick={() => setShowAll(true)}
          >
            Show all books
          </button>
        </div>
      )}

      {!lib.loading && files !== null && browsing && (
        <BookList
          rows={rows}
          allRows={allRows}
          totalCount={files.length}
          sort={sort}
          token={token}
          seriesGaps={seriesGaps}
          selected={selected}
          expandedId={expandedId}
          sentMap={sentMap}
          koboDevices={koboDevices}
          sendState={sendState}
          emptyMessage={emptyMessage}
          onToggleSelect={toggleSelected}
          onSelectMany={selectMany}
          onExpand={(id) => setExpandedId((cur) => (cur === id ? null : id))}
          onSend={sendToKobo}
          onDownload={(file) =>
            downloadFile(token, file)
              .then(() => logDownload(file))
              .catch((err) => {
                lib.flagAuthError(err)
                setDownloadError(err.message)
              })
          }
          onFilterAuthor={(a) => filterTo(a, 'author')}
          onFilterSeries={(s) => filterTo(s, 'series')}
        />
      )}
    </div>
  )
}
