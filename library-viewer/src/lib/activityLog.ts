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

import { readJsonFile, writeJsonFile } from './drive'

const FILENAME = 'bookbrain-activity-log.json'
const MAX_EVENTS = 500

export type ActivityEventType =
  | 'sign-in'
  | 'search'
  | 'download'
  | 'kobo-send'
  | 'request'
  | 'read'

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

// Pending events, flushed together ~1s after the last one. Clicking several
// "Request" buttons in a row otherwise fires a read+write per click — on top
// of the wishlist's own writes, enough to trip Drive's per-user rate limit.
let queued: { who: string; type: ActivityEventType; detail: string; at: string }[] = []
let flushTimer: ReturnType<typeof setTimeout> | null = null
let lastArgs: { token: string; folderId: string } | null = null

async function flushActivity(): Promise<void> {
  flushTimer = null
  if (!queued.length || !lastArgs) return
  const batch = queued
  queued = []
  const { token, folderId } = lastArgs
  try {
    const found = await readJsonFile<RawFile>(token, folderId, FILENAME)
    const existing = (found?.content.events ?? []).filter(isValidEvent)
    const events = [
      ...existing,
      ...batch.map((e) => ({
        id: `${Date.parse(e.at)}-${Math.random().toString(36).slice(2, 8)}`,
        type: e.type,
        who: e.who,
        at: e.at,
        detail: e.detail,
      })),
    ].slice(-MAX_EVENTS)
    await writeJsonFile(token, folderId, FILENAME, { version: 1, events }, found?.id ?? null)
  } catch {
    // Best-effort — losing a log line is fine, never block the real action.
  }
}

export async function logActivity(
  token: string,
  folderId: string,
  who: string,
  type: ActivityEventType,
  detail: string,
): Promise<void> {
  lastArgs = { token, folderId }
  queued.push({ who, type, detail, at: new Date().toISOString() })
  if (flushTimer) clearTimeout(flushTimer)
  flushTimer = setTimeout(() => void flushActivity(), 1000)
}
