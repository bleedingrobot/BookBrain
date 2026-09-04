import { useCallback, useEffect, useRef, useState } from 'react'
import { clearCoverCache, loadCoverManifest } from '../lib/covers'
import { isAuthError, type DriveFile } from '../lib/drive'
import { requestAccessToken, SCOPE_FULL, SCOPE_READONLY } from '../lib/googleAuth'
import {
  clearCachedIndex,
  EMPTY_INDEX,
  fetchLibraryIndex,
  loadCachedIndex,
  type LibraryIndex,
} from '../lib/libraryIndex'
import { clearLibraryCache, loadCachedFiles, syncLibrary } from '../lib/librarySync'
import type { ViewerSettings } from '../lib/settings'

const REFRESH_LEAD_MS = 120_000 // renew the token this long before it lapses

// Everything about the Google session and the library/index/cover data: the
// access token and its silent renewal, the file list + metadata sidecar,
// and the sign-in / refresh / rebuild actions. App owns settings and the
// screen routing; this owns the data.
export function useLibrary(settings: ViewerSettings | null) {
  const [token, setToken] = useState<string | null>(null)
  const [tokenExpiresAt, setTokenExpiresAt] = useState(0)
  const [sessionExpired, setSessionExpired] = useState(false)
  const [signingIn, setSigningIn] = useState(false)
  const [authError, setAuthError] = useState<string | null>(null)

  const [files, setFiles] = useState<DriveFile[] | null>(null)
  const [index, setIndex] = useState<LibraryIndex>(EMPTY_INDEX)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [syncing, setSyncing] = useState(false)
  const [syncMessage, setSyncMessage] = useState<string | null>(null)

  const scope = settings?.readOnly ? SCOPE_READONLY : SCOPE_FULL

  const flagAuthError = useCallback((err: unknown) => {
    if (isAuthError(err)) setSessionExpired(true)
  }, [])

  const applyToken = useCallback((newToken: string, expiresInSeconds: number) => {
    setToken(newToken)
    setTokenExpiresAt(Date.now() + expiresInSeconds * 1000)
    setSessionExpired(false)
  }, [])

  const runSync = useCallback(
    async (activeToken: string) => {
      if (!settings) return
      setLoadError(null)
      try {
        const { cache, rebuilt } = await syncLibrary(activeToken, settings.libraryFolderId)
        setFiles(cache.files)
        setSyncMessage(rebuilt ? `Library built — ${cache.files.length} books.` : 'Synced.')
      } catch (err) {
        flagAuthError(err)
        setLoadError(err instanceof Error ? err.message : 'Failed to load your library.')
      }
      const idx = await fetchLibraryIndex(activeToken, settings.libraryFolderId)
      setIndex(idx)
      await loadCoverManifest(activeToken, idx.coversFolder)
    },
    [settings, flagAuthError],
  )

  // Keep the freshest runSync around so the silent-refresh timer and the
  // sign-in callback don't capture a stale one.
  const runSyncRef = useRef(runSync)
  runSyncRef.current = runSync

  const signIn = useCallback(() => {
    if (!settings) return
    setAuthError(null)
    setSigningIn(true)
    requestAccessToken(
      settings.googleClientId,
      scope,
      async (newToken, expiresIn) => {
        setSigningIn(false)
        applyToken(newToken, expiresIn)
        const cached = loadCachedFiles(settings.libraryFolderId)
        if (cached) {
          setFiles(cached)
          setIndex(loadCachedIndex(settings.libraryFolderId))
          setSyncing(true)
          await runSyncRef.current(newToken)
          setSyncing(false)
        } else {
          setLoading(true)
          await runSyncRef.current(newToken)
          setLoading(false)
        }
      },
      (message) => {
        setSigningIn(false)
        setAuthError(message)
      },
    )
  }, [settings, scope, applyToken])

  // Silent renewal shortly before the token lapses.
  useEffect(() => {
    if (!token || !settings || !tokenExpiresAt) return
    const delay = Math.max(10_000, tokenExpiresAt - Date.now() - REFRESH_LEAD_MS)
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
  }, [token, tokenExpiresAt, settings, scope, applyToken])

  const refresh = useCallback(async () => {
    if (!token) return
    setSyncing(true)
    await runSync(token)
    setSyncing(false)
  }, [token, runSync])

  const rebuild = useCallback(async () => {
    if (!token) return
    clearLibraryCache()
    clearCachedIndex()
    clearCoverCache()
    setLoading(true)
    await runSync(token)
    setLoading(false)
  }, [token, runSync])

  // Drop every trace of the session and its data (App clears settings).
  const reset = useCallback(() => {
    clearLibraryCache()
    clearCachedIndex()
    clearCoverCache()
    setToken(null)
    setTokenExpiresAt(0)
    setFiles(null)
    setIndex(EMPTY_INDEX)
  }, [])

  return {
    token,
    scope,
    sessionExpired,
    signingIn,
    authError,
    files,
    index,
    loadError,
    loading,
    syncing,
    syncMessage,
    signIn,
    refresh,
    rebuild,
    reset,
    flagAuthError,
  }
}
