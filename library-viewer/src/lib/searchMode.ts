// Keyword vs. meaning (semantic) search — a per-viewer toggle by the search
// box, remembered in localStorage like the viewer name. Not in the Drive
// settings file: it's a personal preference, not shared household config.

const KEY = 'bookbrain.searchMode'

export type SearchMode = 'keyword' | 'meaning'

export function getSearchMode(): SearchMode {
  try {
    return localStorage.getItem(KEY) === 'meaning' ? 'meaning' : 'keyword'
  } catch {
    return 'keyword'
  }
}

export function setSearchMode(mode: SearchMode): void {
  try {
    if (mode === 'meaning') localStorage.setItem(KEY, 'meaning')
    else localStorage.removeItem(KEY)
  } catch {
    /* private mode — the toggle just won't stick */
  }
}
