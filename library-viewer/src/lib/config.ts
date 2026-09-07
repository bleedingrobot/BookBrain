// Build-time defaults so the deployed viewer needs zero setup: open the
// page, hit "Sign in with Google", done. Both values are safe to ship in a
// public bundle — the OAuth Client ID is a public identifier (no secret is
// involved in the browser token flow, see googleAuth.ts) and the library
// folder ID is just the id segment from a Drive folder URL.
//
// Anything saved in this browser's localStorage still wins over these (see
// settings.loadPartialSettings), so a device pointed at a different library
// via the setup form or a ?clientId=&folderId= link is unaffected.

export const DEFAULT_GOOGLE_CLIENT_ID =
  '338765367468-j4mko9e6o33urrhcbeo4flbv6fodqvqr.apps.googleusercontent.com'

export const DEFAULT_LIBRARY_FOLDER_ID = '1BdqKbxECXkg70DZRXg3ynzxEO2hK3SrH'

// Optional. A Google Books API key lifts wishlist search off the shared
// keyless quota (one bucket per public IP across every anonymous caller —
// it 429s constantly). Also safe to ship in the public bundle IF the key is
// restricted in the Google Cloud console to (a) HTTP referrers matching this
// site's origin and (b) the Books API only: it's read-only public data and
// the referrer lock stops it being reused elsewhere. Left empty = use the
// keyless endpoint and lean on the Open Library fallback (see bookSearch.ts).
export const DEFAULT_GOOGLE_BOOKS_API_KEY = ''
