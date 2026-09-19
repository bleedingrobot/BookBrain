import { useCallback, useEffect, useRef, useState } from 'react'
import { logActivity } from '../lib/activityLog'
import { clearCoverCache, loadCoverManifest } from '../lib/covers'
import { isAuthError, type DriveFile } from '../lib/drive'
import { requestAccessToken, SCOPE_FULL } from '../lib/googleAuth'
import { setTokenRefresher } from '../lib/tokenBroker'
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
import { getAuthMode, PASSCODE_TOKEN, setAuthMode, type AuthMode } from '../lib/viewerAuth'
import { hasPasscodeSession, loginWithPasscode, logoutPasscode } from '../lib/viewerSession'

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

  // Which login path is active — see lib/viewerAuth.ts. Mirrored into React
  // state (the module-level flag itself is what drive.ts actually reads)
  // just so the UI can branch on it too.
  const [authMode, setAuthModeState] = useState<AuthMode>(getAuthMode())
  const [passcodeSigningIn, setPasscodeSigningIn] = useState(false)
  const [passcodeError, setPasscodeError] = useState<string | null>(null)

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
    if (!isAuthError(err)) return
    if (getAuthMode() === 'passcode') {
      // No silent reconnect for a passcode session (the passcode itself is
      // never kept around client-side) — drop straight back to the login
      // screen instead of showing a "Reconnect" button with nothing to do.
      setToken(null)
    } else {
      setSessionExpired(true)
    }
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
    setAuthMode('google')
    setAuthModeState('google')
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
        const cached = await loadCachedFiles(settings.libraryFolderId)
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

  // Runs the same first-load sequence as signIn's callback above (seed from
  // cache if there is one, else a full sync) — factored out so both the
  // passcode login and the auto-resume effect below can share it.
  const startLibraryLoad = useCallback(
    async (activeToken: string) => {
      if (!settings) return
      const who = getViewerName()
      if (who) void logActivity(activeToken, settings.libraryFolderId, who, 'sign-in', '')
      const cached = await loadCachedFiles(settings.libraryFolderId)
      if (cached) {
        setFiles(cached)
        setIndex(loadCachedIndex(settings.libraryFolderId))
        setSyncing(true)
        await runSyncRef.current(activeToken)
        setSyncing(false)
      } else {
        setLoading(true)
        await runSyncRef.current(activeToken)
        setLoading(false)
      }
    },
    [settings],
  )

  // The passcode login (App.tsx's passcode field) — no Google token, no
  // silent renewal: the session lives entirely in the httpOnly cookie
  // loginWithPasscode sets, which the browser attaches to every /api/viewer
  // call on its own. tokenExpiresAt stays 0, which is what keeps the
  // Google-only silent-renewal effect below a no-op for this path.
  const signInWithPasscode = useCallback(
    async (passcode: string) => {
      setPasscodeError(null)
      setPasscodeSigningIn(true)
      try {
        await loginWithPasscode(passcode)
        setAuthMode('passcode')
        setAuthModeState('passcode')
        setSessionExpired(false)
        setToken(PASSCODE_TOKEN)
        await startLibraryLoad(PASSCODE_TOKEN)
      } catch (err) {
        setPasscodeError(err instanceof Error ? err.message : 'Sign-in failed.')
      } finally {
        setPasscodeSigningIn(false)
      }
    },
    [startLibraryLoad],
  )

  // A returning sibling shouldn't have to retype the household passcode on
  // every visit the way Google sign-in intentionally requires (see App.tsx's
  // "closing or reloading this page signs you out" copy) — the session
  // cookie is deliberately long-lived (see backend's VIEWER_SESSION_DAYS)
  // specifically so this can auto-resume. Runs once per mount; harmless (one
  // cheap GET, resolves false immediately) when the passcode login isn't
  // configured or wasn't used.
  useEffect(() => {
    if (!settings) return
    let cancelled = false
    void hasPasscodeSession().then((ok) => {
      if (cancelled || !ok) return
      setAuthMode('passcode')
      setAuthModeState('passcode')
      setToken(PASSCODE_TOKEN)
      void startLibraryLoad(PASSCODE_TOKEN)
    })
    return () => {
      cancelled = true
    }
    // Only ever needs to run once settings first resolve, not on every
    // startLibraryLoad identity change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settings])

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

  // Let the low-level Drive helpers force a token refresh + retry when a
  // write comes back 401 (the timer above can miss if the tab slept).
  useEffect(() => {
    if (!settings) return
    setTokenRefresher(
      () =>
        new Promise<string>((resolve, reject) => {
          requestAccessToken(
            settings.googleClientId,
            scope,
            (t, exp) => {
              applyToken(t, exp)
              resolve(t)
            },
            (message) => {
              setSessionExpired(true)
              reject(new Error(message || 'Sign-in expired — sign in again.'))
            },
            { silent: true },
          )
        }),
    )
    return () => setTokenRefresher(null)
  }, [settings, scope, applyToken])

  const refresh = useCallback(async () => {
    if (!token) return
    setSyncing(true)
    await runSync(token)
    setSyncing(false)
  }, [token, runSync])

  const rebuild = useCallback(async () => {
    if (!token) return
    await clearLibraryCache()
    clearCachedIndex()
    clearCoverCache()
    setLoading(true)
    await runSync(token)
    setLoading(false)
  }, [token, runSync])

  // Drop every trace of the session and its data (App clears settings).
  const reset = useCallback(() => {
    void clearLibraryCache()
    clearCachedIndex()
    clearCoverCache()
    void logoutPasscode()
    setAuthMode('google')
    setAuthModeState('google')
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
    authMode,
    sessionExpired,
    signingIn,
    authError,
    passcodeSigningIn,
    passcodeError,
    files,
    index,
    loadError,
    loading,
    syncing,
    syncMessage,
    remoteKoboDevices,
    saveKoboDevices,
    signIn,
    signInWithPasscode,
    refresh,
    rebuild,
    reset,
    flagAuthError,
  }
}
