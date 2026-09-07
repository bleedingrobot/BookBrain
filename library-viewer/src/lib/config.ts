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
