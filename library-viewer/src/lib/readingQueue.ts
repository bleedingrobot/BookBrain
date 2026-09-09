// bookbrain-reading-pending.json — reading-status changes the viewer made
// (marked a book read, etc.) that haven't reached Hardcover yet. The viewer
// is a static site and can't call Hardcover, so it queues here; the backend's
// reading sync applies each to Hardcover and drops the ones that landed
// (prompts/30 Phase 3). Until then the viewer overlays these on the reading
// badges so a mark shows up straight away.

import { readJsonFile, writeJsonFile } from './drive'
import type { ReadingStatus } from './reading'

const FILENAME = 'bookbrain-reading-pending.json'

export interface ReadingChange {
  driveFileId: string
  isbn13: string | null
  title: string
  author: string | null
  // A change carries a status, a reader position (prompts/31 Part I), or both.
  status?: ReadingStatus
  progressPercent?: number
  at: string
  by: string
}

interface RawFile {
  version?: number
  changes?: Partial<ReadingChange>[]
}

// load → merge into any earlier change for this book (so a later progress
// push doesn't drop an unsynced status change, or vice versa) → save.
// Best-effort, same as the wishlist: two racing writes could clobber.
export async function queueReadingChange(
  token: string,
  libraryFolderId: string,
  change: ReadingChange,
): Promise<void> {
  const found = await readJsonFile<RawFile>(token, libraryFolderId, FILENAME)
  const all = (found?.content.changes ?? []).filter(
    (c): c is ReadingChange => !!c && typeof c.driveFileId === 'string',
  )
  const prev = all.find((c) => c.driveFileId === change.driveFileId)
  const merged: ReadingChange = {
    ...prev,
    ...change,
    status: change.status ?? prev?.status,
    progressPercent: change.progressPercent ?? prev?.progressPercent,
  }
  const kept = all.filter((c) => c.driveFileId !== change.driveFileId)
  await writeJsonFile(
    token,
    libraryFolderId,
    FILENAME,
    { version: 1, changes: [...kept, merged] },
    found?.id ?? null,
  )
}

// driveFileId → the pending status, for the optimistic overlay.
export async function loadPendingReading(
  token: string,
  libraryFolderId: string,
): Promise<Map<string, ReadingStatus>> {
  try {
    const found = await readJsonFile<RawFile>(token, libraryFolderId, FILENAME)
    const out = new Map<string, ReadingStatus>()
    for (const c of found?.content.changes ?? []) {
      if (c && typeof c.driveFileId === 'string' && c.status) {
        out.set(c.driveFileId, c.status as ReadingStatus)
      }
    }
    return out
  } catch {
    return new Map()
  }
}
