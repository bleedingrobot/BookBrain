import { useCallback, useEffect, useRef, useState } from 'react'
import { logActivity } from '../lib/activityLog'
import { clearCoverCache, loadCoverManifest } from '../lib/covers'
import { isAuthError, type DriveFile } from '../lib/drive'
import { requestAccessToken, SCOPE_FULL } from '../lib/googleAuth'
import { loadRemoteKoboDevices, saveRemoteKoboDevices } from '../lib/koboDeviceSync'
import { getViewerName } from '../lib/viewerIdentity'
import {
  clearCachedIndex,
  EMPTY_INDEX,
  fetchLibraryIndex,
  loadCachedIndex,
  type LibraryIndex,
} from '../lib/libraryIndex'
import { clearLibraryCache, loadCachedFiles, syncLibrary } from '../lib/librarySync'
import type { KoboDevice, ViewerSettings } from '../lib/settings'

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

  // Kobo device folder ids, synced via a small JSON file in the library
  // folder — null until the first sync resolves (App falls back to
  // settings.koboDevices until then, so nothing regresses while this is
  // still in flight). Not held in React state for the write side: writes
  // just need the latest fileId, not a re-render, so a ref avoids stale
  // closures without adding another effect dependency.
  const [remoteKoboDevices, setRemoteKoboDevices] = useState<KoboDevice[] | null>(null)
  const remoteSettingsFileIdRef = useRef<string | null>(null)

  const scope = SCOPE_FULL

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

      try {
        const remote = await loadRemoteKoboDevices(activeToken, settings.libraryFolderId)
        if (remote.devices !== null) {
          remoteSettingsFileIdRef.current = remote.fileId
          setRemoteKoboDevices(remote.devices)
        } else if (settings.koboDevices && settings.koboDevices.length > 0) {
          // No settings file in this library yet, but this browser has
          // devices configured locally (the pre-sync setup, or a device
          // added before this feature existed) — seed Drive from them so
          // the next device/browser that signs in gets them for free
          // instead of retyping the same folder ids.
          const fileId = await saveRemoteKoboDevices(
            activeToken,
            settings.libraryFolderId,
            settings.koboDevices,
            null,
          )
          remoteSettingsFileIdRef.current = fileId
          setRemoteKoboDevices(settings.koboDevices)
        }
      } catch {
        // Best-effort — settings.koboDevices (App's fallback) still works.
      }
    },
    [settings, flagAuthError],
  )

  // Called when Settings is saved with Kobo devices changed — pushes the
  // new list to Drive so it's not just sitting in this one browser's
  // localStorage. Silently gives up on failure; the local save (App's
  // saveSettings) already went through, so nothing is lost, it just won't
  // show up on another device until the next successful sync.
  const saveKoboDevices = useCallback(
    async (devices: KoboDevice[]) => {
      if (!token || !settings) return
      try {
        const fileId = await saveRemoteKoboDevices(
          token,
          settings.libraryFolderId,
          devices,
          remoteSettingsFileIdRef.current,
        )
        remoteSettingsFileIdRef.current = fileId
        setRemoteKoboDevices(devices)
      } catch (err) {
        flagAuthError(err)
      }
    },
    [token, settings, flagAuthError],
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
        const who = getViewerName()
        if (who) void logActivity(newToken, settings.libraryFolderId, who, 'sign-in', '')
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
    setRemoteKoboDevices(null)
    remoteSettingsFileIdRef.current = null
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
    remoteKoboDevices,
    saveKoboDevices,
    signIn,
    refresh,
    rebuild,
    reset,
    flagAuthError,
  }
}
