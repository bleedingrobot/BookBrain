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
  status: ReadingStatus
  at: string
  by: string
}

interface RawFile {
  version?: number
  changes?: Partial<ReadingChange>[]
}

// load → drop any earlier change for this book → append → save. Best-effort,
// same as the wishlist: two racing writes could clobber, acceptable here.
export async function queueReadingChange(
  token: string,
  libraryFolderId: string,
  change: ReadingChange,
): Promise<void> {
  const found = await readJsonFile<RawFile>(token, libraryFolderId, FILENAME)
  const kept = (found?.content.changes ?? []).filter(
    (c): c is ReadingChange =>
      !!c && typeof c.driveFileId === 'string' && c.driveFileId !== change.driveFileId,
  )
  await writeJsonFile(
    token,
    libraryFolderId,
    FILENAME,
    { version: 1, changes: [...kept, change] },
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
