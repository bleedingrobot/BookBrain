// A tiny indirection so low-level Drive helpers can ask for a fresh access
// token without importing React or the auth hook. `useLibrary` registers a
// refresher on mount (a silent GIS token request that also pushes the new
// token into React state); `refreshAccessToken` is what `drive.ts` calls when
// a write comes back 401.

type Refresher = () => Promise<string>

let refresher: Refresher | null = null
let inFlight: Promise<string> | null = null

export function setTokenRefresher(fn: Refresher | null): void {
  refresher = fn
}

// Resolves with a fresh access token, or rejects if there's no refresher or
// the silent renewal fails (session genuinely gone). Concurrent callers share
// one refresh.
export function refreshAccessToken(): Promise<string> {
  if (inFlight) return inFlight
  if (!refresher) return Promise.reject(new Error('Sign-in expired — sign in again.'))
  inFlight = refresher().finally(() => {
    inFlight = null
  })
  return inFlight
}
