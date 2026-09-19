// A local, per-browser record of which library books have been sent to which
// Kobo device — keyed by the *library* Drive file id (what the viewer shows)
// and the device's sync-folder id. "Send to Kobo" copies the file, so the
// copy has its own id; this tracks the source so the tick survives across
// sessions. It's a best-effort convenience, not a source of truth: removing
// a book on the "what's on the device" screen clears its entry, but a book
// deleted straight from Drive won't.

const KEY = 'bookbrain.sentToDevice'

// { [koboFolderId]: { [libraryDriveId]: ISO-8601 sent-at } }
export type SentMap = Record<string, Record<string, string>>

function read(): SentMap {
  try {
    const raw = localStorage.getItem(KEY)
    return raw ? (JSON.parse(raw) as SentMap) : {}
  } catch {
    return {}
  }
}

function write(map: SentMap): SentMap {
  try {
    localStorage.setItem(KEY, JSON.stringify(map))
  } catch {
    // storage full / disabled — the tick just won't persist, no worse than before
  }
  return map
}

export function getSentMap(): SentMap {
  return read()
}

export function markSent(folderId: string, libraryDriveIds: string[]): SentMap {
  if (libraryDriveIds.length === 0) return read()
  const map = read()
  const bucket = (map[folderId] ??= {})
  const now = new Date().toISOString()
  for (const id of libraryDriveIds) bucket[id] = now
  return write(map)
}

export function unmarkSent(folderId: string, libraryDriveIds: string[]): SentMap {
  const map = read()
  const bucket = map[folderId]
  if (!bucket) return map
  for (const id of libraryDriveIds) delete bucket[id]
  return write(map)
}

export function clearSentTracker(): void {
  localStorage.removeItem(KEY)
}
