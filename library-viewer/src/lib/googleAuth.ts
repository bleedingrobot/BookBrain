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

const SCOPE = 'https://www.googleapis.com/auth/drive.readonly'

let tokenClient: TokenClient | null = null
let currentClientId: string | null = null

function getTokenClient(clientId: string, onToken: (token: string) => void, onError: (message: string) => void): TokenClient {
  if (tokenClient && currentClientId === clientId) return tokenClient
  if (!window.google) {
    throw new Error('Google sign-in script has not loaded yet — try again in a moment.')
  }
  tokenClient = window.google.accounts.oauth2.initTokenClient({
    client_id: clientId,
    scope: SCOPE,
    callback: (resp) => {
      if (resp.error || !resp.access_token) {
        onError(resp.error || 'Sign-in failed.')
        return
      }
      onToken(resp.access_token)
    },
    error_callback: (err) => onError(err.type || 'Sign-in failed.'),
  })
  currentClientId = clientId
  return tokenClient
}

export function requestAccessToken(
  clientId: string,
  onToken: (token: string) => void,
  onError: (message: string) => void,
): void {
  try {
    const client = getTokenClient(clientId, onToken, onError)
    client.requestAccessToken()
  } catch (err) {
    onError(err instanceof Error ? err.message : 'Sign-in failed.')
  }
}
