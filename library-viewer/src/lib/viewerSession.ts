// The passcode login itself (POST /api/viewer/session) — separate from
// drive.ts's Drive-shaped proxy calls, which all assume a session already
// exists. Every call here goes to our own backend, never Google.

import { BACKEND_URL } from './config'

export class BackendNotConfiguredError extends Error {
  constructor() {
    super("The library owner hasn't set up the passcode login yet.")
  }
}

function requireBackendUrl(): string {
  if (!BACKEND_URL) throw new BackendNotConfiguredError()
  return BACKEND_URL
}

// Resolves on a correct passcode (the session cookie is now set), rejects
// with a message fit to show under the passcode field otherwise.
export async function loginWithPasscode(passcode: string): Promise<void> {
  const base = requireBackendUrl()
  let response: Response
  try {
    response = await fetch(`${base}/api/viewer/session`, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ passcode }),
    })
  } catch {
    throw new Error("Couldn't reach the library server — check your connection.")
  }
  if (response.status === 401) throw new Error('Wrong passcode.')
  if (!response.ok) throw new Error(`Sign-in failed (${response.status}).`)
}

// Whether a passcode session cookie is currently valid — used on load so a
// returning sibling doesn't have to retype the passcode every visit.
export async function hasPasscodeSession(): Promise<boolean> {
  if (!BACKEND_URL) return false
  try {
    const response = await fetch(`${BACKEND_URL}/api/viewer/session`, { credentials: 'include' })
    if (!response.ok) return false
    const data = (await response.json()) as { authenticated: boolean }
    return data.authenticated
  } catch {
    return false
  }
}

export async function logoutPasscode(): Promise<void> {
  if (!BACKEND_URL) return
  try {
    await fetch(`${BACKEND_URL}/api/viewer/session`, { method: 'DELETE', credentials: 'include' })
  } catch {
    // best-effort — an expired cookie is harmless either way
  }
}
