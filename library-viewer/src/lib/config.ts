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

// A Google Books API key lifts wishlist search off the shared keyless quota
// (one bucket per public IP across every anonymous caller — it 429s
// constantly). Safe to ship in the public bundle: it's restricted in the
// Google Cloud console to HTTP referrers matching https://bleedingrobot.github.io/*
// and to the Books API only, so it can't be reused elsewhere and only
// touches read-only public data. Verified 2026-09-08 — a request with no /
// a foreign Referer gets 403, this origin gets 200. If quota theft ever
// shows up, rotate the key here. Empty = keyless endpoint + Open Library
// fallback (see bookSearch.ts).
export const DEFAULT_GOOGLE_BOOKS_API_KEY = 'AIzaSyARc-3eRiMVr4EKC-UGTZ1RyHDv8eAgODU'

// The backend's own public URL — only the passcode login path needs this
// (Google sign-in talks to Drive directly and never involves our backend at
// all). Unlike the values above, this can't ship a working default: it's
// not a public identifier, it's wherever bookbrain.service is actually
// reachable from the internet, which has to be set up (a domain + HTTPS,
// e.g. a reverse proxy or Tailscale Funnel) before the passcode option can
// work for anyone outside the house. Empty means "not configured yet" — the
// passcode tab shows that plainly instead of failing at fetch() with a
// confusing network error.
export const BACKEND_URL = ''
