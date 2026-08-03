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

export default function App() {
  const [showSetup, setShowSetup] = useState(false)
  const [settings, setSettings] = useState<ViewerSettings | null>(loadSettings)
  const [token, setToken] = useState<string | null>(null)
  const [authError, setAuthError] = useState<string | null>(null)
  const [signingIn, setSigningIn] = useState(false)

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

  if (showSetup) {
    return <SetupChecklist onBack={() => setShowSetup(false)} />
  }

  if (!settings) {
    return (
      <>
        <SettingsForm initial={loadPartialSettings()} onSave={(s) => { saveSettings(s); setSettings(s) }} />
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
    try {
      for (const { file } of books.filter((b) => selected.has(b.file.id))) {
        await downloadFile(token, file)
        await sleep(300) // browsers throttle/block rapid-fire simultaneous downloads
      }
      setSelected(new Set())
    } catch (err) {
      setDownloadError(err instanceof Error ? err.message : 'A download failed.')
    } finally {
      setDownloading(false)
    }
  }

  async function sendToKobo(file: DriveFile) {
    if (!token) return
    setKoboError(null)
    setKoboMessage(null)
    try {
      await copyFileToFolder(token, file, settings!.koboFolderId)
      setKoboMessage(`Sent "${file.name}" to Kobo.`)
    } catch (err) {
      setKoboError(err instanceof Error ? err.message : 'Failed to send to Kobo.')
    }
  }

  async function handleSendSelectedToKobo() {
    if (!token) return
    setSendingToKobo(true)
    setKoboError(null)
    setKoboMessage(null)
    const toSend = books.filter((b) => selected.has(b.file.id))
    try {
      for (const { file } of toSend) {
        await copyFileToFolder(token, file, settings!.koboFolderId)
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
        <button
          className="mt-8 block w-full text-xs text-neutral-400 underline"
          onClick={() => {
            clearSettings()
            setSettings(null)
          }}
        >
          Change settings
        </button>
        <button
          className="mt-2 block w-full text-xs text-neutral-400 underline"
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
          <button
            className="underline"
            onClick={() => {
              clearSettings()
              setSettings(null)
              setToken(null)
              setFiles(null)
            }}
          >
            Change settings
          </button>
          <button className="underline" onClick={() => setShowSetup(true)}>
            Recovery checklist
          </button>
        </div>
      </div>

      {syncMessage && !syncing && !loading && (
        <p className="mt-1 text-xs text-neutral-400">{syncMessage}</p>
      )}

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
          <button
            className="rounded border border-neutral-300 px-3 py-1.5 text-xs disabled:opacity-50 dark:border-neutral-700"
            disabled={sendingToKobo}
            onClick={handleSendSelectedToKobo}
          >
            {sendingToKobo ? 'Sending…' : `Send ${selected.size} book${selected.size === 1 ? '' : 's'} to Kobo`}
          </button>
          {downloadError && <span className="text-xs text-red-600">{downloadError}</span>}
          {koboError && <span className="text-xs text-red-600">{koboError}</span>}
        </div>
      )}
      {selected.size === 0 && koboMessage && (
        <p className="mt-4 text-xs text-neutral-500">{koboMessage}</p>
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
            <button
              className="shrink-0 rounded border border-neutral-300 px-2 py-1 text-xs dark:border-neutral-700"
              onClick={() => sendToKobo(file)}
            >
              Send to Kobo
            </button>
          </li>
        ))}
        {!loading && query.trim() !== '' && books.length === 0 && (
          <li className="py-4 text-neutral-400">No books match your search.</li>
        )}
      </ul>
    </div>
  )
}
