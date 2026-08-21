import { useMemo, useState } from 'react'
import { SettingsForm } from './components/SettingsForm'
import { SetupChecklist } from './components/SetupChecklist'
import { copyFileToFolder, downloadFile, type DriveFile } from './lib/drive'
import { requestAccessToken } from './lib/googleAuth'
import { clearLibraryCache, loadCachedFiles, syncLibrary } from './lib/librarySync'
import { matchesSearch, parseFilename } from './lib/parseFilename'
import { clearSettings, loadPartialSettings, loadSettings, saveSettings, type ViewerSettings } from './lib/settings'

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
  const [editingSettings, setEditingSettings] = useState(false)
  const [settings, setSettings] = useState<ViewerSettings | null>(() => consumeSharedSettings() ?? loadSettings())
  const [token, setToken] = useState<string | null>(null)
  const [authError, setAuthError] = useState<string | null>(null)
  const [signingIn, setSigningIn] = useState(false)
  const [shareStatus, setShareStatus] = useState<string | null>(null)

  const [files, setFiles] = useState<DriveFile[] | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false) // first-ever build, nothing cached to show meanwhile
  const [syncing, setSyncing] = useState(false) // background refresh, cached list already shown
  const [syncMessage, setSyncMessage] = useState<string | null>(null)

  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [downloading, setDownloading] = useState(false)
  const [downloadError, setDownloadError] = useState<string | null>(null)
  const [sendingToKobo, setSendingToKobo] = useState(false)
  const [koboError, setKoboError] = useState<string | null>(null)
  const [koboMessage, setKoboMessage] = useState<string | null>(null)

  const books = useMemo(
    () =>
      (files ?? [])
        .map((file) => ({ file, parsed: parseFilename(file.name) }))
        .filter(({ file, parsed }) => matchesSearch(parsed, file.name, query))
        .sort((a, b) => a.parsed.title.localeCompare(b.parsed.title)),
    [files, query],
  )

  const hasKobo = Boolean(settings?.koboFolderId)

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
            // folder ID (or re-saving unchanged values to back out of
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
    setToken(null)
    setFiles(null)
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

  async function handleDownloadSelected() {
    if (!token) return
    setDownloading(true)
    setDownloadError(null)
    const toDownload = books.filter((b) => selected.has(b.file.id))
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

  async function sendToKobo(file: DriveFile) {
    if (!token || !settings?.koboFolderId) return
    setKoboError(null)
    setKoboMessage(null)
    try {
      await copyFileToFolder(token, file, settings.koboFolderId)
      setKoboMessage(`Sent "${file.name}" to Kobo.`)
    } catch (err) {
      setKoboError(err instanceof Error ? err.message : 'Failed to send to Kobo.')
    }
  }

  async function handleSendSelectedToKobo() {
    if (!token || !settings?.koboFolderId) return
    const koboFolderId = settings.koboFolderId
    setSendingToKobo(true)
    setKoboError(null)
    setKoboMessage(null)
    const toSend = books.filter((b) => selected.has(b.file.id))
    try {
      for (const { file } of toSend) {
        await copyFileToFolder(token, file, koboFolderId)
      }
      setKoboMessage(`Sent ${toSend.length} book${toSend.length === 1 ? '' : 's'} to Kobo.`)
      setSelected(new Set())
    } catch (err) {
      setKoboError(err instanceof Error ? err.message : 'Failed to send to Kobo.')
    } finally {
      setSendingToKobo(false)
    }
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

      <input
        className="mt-4 w-full rounded border border-neutral-300 px-3 py-2 text-sm dark:border-neutral-700 dark:bg-neutral-900"
        placeholder="Search title, author, or series…"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />

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
          {hasKobo && (
            <button
              className="rounded border border-neutral-300 px-3 py-1.5 text-xs disabled:opacity-50 dark:border-neutral-700"
              disabled={sendingToKobo}
              onClick={handleSendSelectedToKobo}
            >
              {sendingToKobo ? 'Sending…' : `Send ${selected.size} book${selected.size === 1 ? '' : 's'} to Kobo`}
            </button>
          )}
          {downloadError && <span className="text-xs text-red-600">{downloadError}</span>}
          {hasKobo && koboError && <span className="text-xs text-red-600">{koboError}</span>}
        </div>
      )}
      {selected.size === 0 && hasKobo && (koboError || koboMessage) && (
        <p className={`mt-4 text-xs ${koboError ? 'text-red-600' : 'text-neutral-500'}`}>
          {koboError ?? koboMessage}
        </p>
      )}

      <ul className="mt-4 divide-y divide-neutral-100 text-sm dark:divide-neutral-800">
        {!loading && query.trim() === '' && files !== null && (
          <li className="py-4 text-neutral-400">
            {files.length === 0
              ? 'No books found in this folder.'
              : `${files.length} book${files.length === 1 ? '' : 's'} in your library — start typing to search.`}
          </li>
        )}
        {query.trim() !== '' && books.map(({ file, parsed }) => (
          <li key={file.id} className="flex items-center gap-3 py-3">
            <input
              type="checkbox"
              checked={selected.has(file.id)}
              onChange={() => toggleSelected(file.id)}
            />
            <div className="min-w-0 flex-1">
              <div className="truncate font-medium">
                {parsed.title}
                {parsed.author && <span className="ml-2 font-normal text-neutral-500">by {parsed.author}</span>}
              </div>
              {parsed.series && (
                <div className="truncate text-xs text-neutral-400">
                  {parsed.series}
                  {parsed.seriesNumber && ` #${parsed.seriesNumber}`}
                </div>
              )}
            </div>
            <button
              className="shrink-0 rounded border border-neutral-300 px-2 py-1 text-xs dark:border-neutral-700"
              onClick={() => downloadFile(token, file).catch((err) => setDownloadError(err.message))}
            >
              Download
            </button>
            {hasKobo && (
              <button
                className="shrink-0 rounded border border-neutral-300 px-2 py-1 text-xs dark:border-neutral-700"
                onClick={() => sendToKobo(file)}
              >
                Send to Kobo
              </button>
            )}
          </li>
        ))}
        {!loading && query.trim() !== '' && books.length === 0 && (
          <li className="py-4 text-neutral-400">No books match your search.</li>
        )}
      </ul>
    </div>
  )
}
