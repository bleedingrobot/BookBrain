import { useMemo, useState } from 'react'
import { SettingsForm } from './components/SettingsForm'
import { downloadFile, listLibraryRecursive, type DriveFile } from './lib/drive'
import { requestAccessToken } from './lib/googleAuth'
import { matchesSearch, parseFilename } from './lib/parseFilename'
import { clearSettings, loadSettings, saveSettings, type ViewerSettings } from './lib/settings'

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

export default function App() {
  const [settings, setSettings] = useState<ViewerSettings | null>(loadSettings)
  const [token, setToken] = useState<string | null>(null)
  const [authError, setAuthError] = useState<string | null>(null)
  const [signingIn, setSigningIn] = useState(false)

  const [files, setFiles] = useState<DriveFile[] | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [downloading, setDownloading] = useState(false)
  const [downloadError, setDownloadError] = useState<string | null>(null)

  const books = useMemo(
    () =>
      (files ?? [])
        .map((file) => ({ file, parsed: parseFilename(file.name) }))
        .filter(({ file, parsed }) => matchesSearch(parsed, file.name, query))
        .sort((a, b) => a.parsed.title.localeCompare(b.parsed.title)),
    [files, query],
  )

  if (!settings) {
    return <SettingsForm initial={null} onSave={(s) => { saveSettings(s); setSettings(s) }} />
  }

  async function handleSignIn() {
    setAuthError(null)
    setSigningIn(true)
    requestAccessToken(
      settings!.googleClientId,
      async (newToken) => {
        setSigningIn(false)
        setToken(newToken)
        setLoading(true)
        setLoadError(null)
        try {
          const listed = await listLibraryRecursive(newToken, settings!.libraryFolderId)
          setFiles(listed)
        } catch (err) {
          setLoadError(err instanceof Error ? err.message : 'Failed to load your library.')
        } finally {
          setLoading(false)
        }
      },
      (message) => {
        setSigningIn(false)
        setAuthError(message)
      },
    )
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
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-2xl p-6">
      <div className="flex items-center justify-between gap-4">
        <h1 className="text-xl font-semibold">BookBrain Library</h1>
        <button
          className="text-xs text-neutral-400 underline"
          onClick={() => {
            clearSettings()
            setSettings(null)
            setToken(null)
            setFiles(null)
          }}
        >
          Change settings
        </button>
      </div>

      <input
        className="mt-4 w-full rounded border border-neutral-300 px-3 py-2 text-sm dark:border-neutral-700 dark:bg-neutral-900"
        placeholder="Search title, author, or series…"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />

      {loading && <p className="mt-6 text-sm text-neutral-500">Loading your library…</p>}
      {loadError && <p className="mt-6 text-sm text-red-600">{loadError}</p>}

      {selected.size > 0 && (
        <div className="mt-4 flex items-center gap-3 rounded border border-neutral-200 p-3 text-sm dark:border-neutral-800">
          <span>{selected.size} selected</span>
          <button
            className="rounded bg-neutral-900 px-3 py-1.5 text-xs text-white disabled:opacity-50 dark:bg-neutral-100 dark:text-neutral-900"
            disabled={downloading}
            onClick={handleDownloadSelected}
          >
            {downloading ? 'Downloading…' : `Download ${selected.size} book${selected.size === 1 ? '' : 's'}`}
          </button>
          {downloadError && <span className="text-xs text-red-600">{downloadError}</span>}
        </div>
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
          </li>
        ))}
        {!loading && query.trim() !== '' && books.length === 0 && (
          <li className="py-4 text-neutral-400">No books match your search.</li>
        )}
      </ul>
    </div>
  )
}
