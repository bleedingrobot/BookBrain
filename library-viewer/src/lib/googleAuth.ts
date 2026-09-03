// Google Identity Services' browser-only OAuth flow — the whole point of
// this app being a static page with no backend. The client ID is a public
// identifier (safe to store in localStorage / ship in a public bundle);
// there is no client secret involved anywhere in this flow.

interface TokenResponse {
  access_token: string
  expires_in: number
  error?: string
}

interface TokenClient {
  requestAccessToken: (opts?: { prompt?: string }) => void
}

declare global {
  interface Window {
    google?: {
      accounts: {
        oauth2: {
          initTokenClient: (config: {
            client_id: string
            scope: string
            callback: (resp: TokenResponse) => void
            error_callback?: (err: { type: string }) => void
          }) => TokenClient
        }
      }
    }
  }
}

// The owner needs full read/write — "Send to Kobo" copies a file into
// another folder (files.copy), which drive.file's narrower scope wouldn't
// cover for pre-existing library files this app never created. A share-link
// guest only ever downloads, so they get the far less alarming
// read-only scope.
export const SCOPE_FULL = 'https://www.googleapis.com/auth/drive'
export const SCOPE_READONLY = 'https://www.googleapis.com/auth/drive.readonly'

let tokenClient: TokenClient | null = null
let currentKey: string | null = null

interface Handlers {
  onToken: (token: string, expiresInSeconds: number) => void
  onError: (message: string) => void
}

function getTokenClient(clientId: string, scope: string, handlers: Handlers): TokenClient {
  const key = `${clientId}|${scope}`
  if (tokenClient && currentKey === key) return tokenClient
  if (!window.google) {
    throw new Error('Google sign-in script has not loaded yet — try again in a moment.')
  }
  tokenClient = window.google.accounts.oauth2.initTokenClient({
    client_id: clientId,
    scope,
    callback: (resp) => {
      if (resp.error || !resp.access_token) {
        handlers.onError(resp.error || 'Sign-in failed.')
        return
      }
      handlers.onToken(resp.access_token, resp.expires_in || 3600)
    },
    error_callback: (err) => handlers.onError(err.type || 'Sign-in failed.'),
  })
  currentKey = key
  return tokenClient
}

export function requestAccessToken(
  clientId: string,
  scope: string,
  onToken: (token: string, expiresInSeconds: number) => void,
  onError: (message: string) => void,
  opts?: { silent?: boolean },
): void {
  try {
    const client = getTokenClient(clientId, scope, { onToken, onError })
    // prompt: '' asks GIS to reuse the existing Google session without a
    // popup — used for the silent pre-expiry refresh.
    client.requestAccessToken(opts?.silent ? { prompt: '' } : undefined)
  } catch (err) {
    onError(err instanceof Error ? err.message : 'Sign-in failed.')
  }
}
