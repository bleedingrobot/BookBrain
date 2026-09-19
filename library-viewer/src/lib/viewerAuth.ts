// Which login path is active — Google OAuth (the browser talks straight to
// Drive with its own token, entirely unchanged) or the shared household
// passcode (no Google token of any kind; every Drive-shaped call instead
// goes through our own backend, which holds its own long-lived Drive
// credential — see app/api/routes/viewer.py). Set once, by whichever path
// signs in (useLibrary.ts); read by drive.ts and the handful of lib/*.ts
// sidecar readers to decide where their calls go.
//
// Module-level like tokenBroker.ts's refresher, for the same reason: the
// low-level Drive helpers are plain functions, not hooks, so they can't read
// this out of React state.

export type AuthMode = 'google' | 'passcode'

let mode: AuthMode = 'google'

export function setAuthMode(next: AuthMode): void {
  mode = next
}

export function getAuthMode(): AuthMode {
  return mode
}

// The `token` parameter threaded through every lib/*.ts function and
// component still needs a non-null string in passcode mode (a lot of call
// sites gate on `if (!token) return`) — this is that placeholder. It is
// never actually sent anywhere; the passcode session lives in an httpOnly
// cookie the browser attaches automatically (`credentials: 'include'`).
export const PASSCODE_TOKEN = 'passcode-session'
