// There's no OAuth identity scope in this app (see googleAuth.ts) — Drive
// tokens don't say which Google account is signed in. For a two-person
// household, self-reporting a name once per browser and remembering it
// locally is simpler than adding an identity scope that forces both people
// to re-consent. This is honesty-based, not verified: it labels activity
// log entries, nothing more.

const NAME_KEY = 'bookbrain.viewerName'

export const SUGGESTED_NAMES = ['James', 'Tess']

export function getViewerName(): string | null {
  return localStorage.getItem(NAME_KEY)
}

export function setViewerName(name: string): void {
  localStorage.setItem(NAME_KEY, name)
}
