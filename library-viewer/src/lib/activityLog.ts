// Who signed in, searched, downloaded, or sent to Kobo — kept as
// bookbrain-activity-log.json in the Drive library folder, same sidecar
// pattern as wishlist.ts and koboDeviceSync.ts. Readable by anyone who can
// open that Drive folder, same as those other files.
//
// writeJsonFile is a full read-modify-write overwrite with no locking, so
// two events logged within moments of each other (from two people, or two
// tabs) can race and one can clobber the other. Fine for a low-volume
// household log where losing an occasional line doesn't matter — never
// worth blocking or retrying the real action over.
//
// A drive.readonly token (share-link guests, see googleAuth.ts) can't write
// this file at all — callers should skip logging entirely for read-only
// sessions rather than attempting a write that will always fail.

import { readJsonFile, writeJsonFile } from './drive'

const FILENAME = 'bookbrain-activity-log.json'
const MAX_EVENTS = 500

export type ActivityEventType = 'sign-in' | 'search' | 'download' | 'kobo-send'

export interface ActivityEvent {
  id: string
  type: ActivityEventType
  who: string
  at: string
  detail: string
}

interface RawFile {
  version?: number
  events?: Partial<ActivityEvent>[]
}

function isValidEvent(e: Partial<ActivityEvent> | undefined): e is ActivityEvent {
  return (
    !!e &&
    typeof e.id === 'string' &&
    typeof e.type === 'string' &&
    typeof e.who === 'string' &&
    typeof e.at === 'string' &&
    typeof e.detail === 'string'
  )
}

export async function loadActivityLog(token: string, folderId: string): Promise<ActivityEvent[]> {
  try {
    const found = await readJsonFile<RawFile>(token, folderId, FILENAME)
    if (!found) return []
    return (found.content.events ?? []).filter(isValidEvent)
  } catch {
    return []
  }
}

export async function logActivity(
  token: string,
  folderId: string,
  who: string,
  type: ActivityEventType,
  detail: string,
): Promise<void> {
  try {
    const found = await readJsonFile<RawFile>(token, folderId, FILENAME)
    const existing = (found?.content.events ?? []).filter(isValidEvent)
    const event: ActivityEvent = {
      id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      type,
      who,
      at: new Date().toISOString(),
      detail,
    }
    const events = [...existing, event].slice(-MAX_EVENTS)
    await writeJsonFile(token, folderId, FILENAME, { version: 1, events }, found?.id ?? null)
  } catch {
    // Best-effort — losing a log line is fine, never block the real action.
  }
}
