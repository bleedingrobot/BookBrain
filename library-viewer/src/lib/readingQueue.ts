// bookbrain-reading-pending.json — reading-status changes the viewer made
// (marked a book read, etc.) that haven't reached Hardcover yet. The viewer
// is a static site and can't call Hardcover, so it queues here; the backend's
// reading sync applies each to Hardcover and drops the ones that landed
// (prompts/30 Phase 3). Until then the viewer overlays these on the reading
// badges so a mark shows up straight away.
//
// A clobbered entry here is a "mark read" that never reaches Hardcover and
// James can't see it, so this sidecar goes through `makeSidecar` — its
// `write` re-reads after saving and reconciles a concurrent write from
// another device (REVIEW-2026-09-10 F3).

import { makeSidecar } from './syncedSidecar'
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

function parse(raw: unknown): ReadingChange[] {
  const changes = (raw as RawFile | null | undefined)?.changes ?? []
  return changes.filter((c): c is ReadingChange => !!c && typeof c.driveFileId === 'string')
}

function serialise(changes: ReadingChange[]): unknown {
  // Canonical order so the concurrency-guard's equality check is exact.
  return {
    version: 1,
    changes: [...changes].sort((a, b) => a.driveFileId.localeCompare(b.driveFileId)),
  }
}

// Combine one change into a list, keeping a status/progress the incoming
// change doesn't carry (a later progress push must not drop an unsynced
// status change, or vice versa).
function upsert(all: ReadingChange[], change: ReadingChange): ReadingChange[] {
  const prev = all.find((c) => c.driveFileId === change.driveFileId)
  const merged: ReadingChange = {
    ...prev,
    ...change,
    status: change.status ?? prev?.status,
    progressPercent: change.progressPercent ?? prev?.progressPercent,
  }
  return [...all.filter((c) => c.driveFileId !== change.driveFileId), merged]
}

// A proper join: union by book; on a conflict the newer `at` wins but keeps a
// status/progress the other side had. Commutative + idempotent, so the
// concurrency guard converges from both devices.
function mergeQueues(a: ReadingChange[], b: ReadingChange[]): ReadingChange[] {
  const byId = new Map<string, ReadingChange>()
  for (const c of [...a, ...b]) {
    const prev = byId.get(c.driveFileId)
    if (!prev) {
      byId.set(c.driveFileId, c)
      continue
    }
    const [newer, older] = (c.at ?? '') >= (prev.at ?? '') ? [c, prev] : [prev, c]
    byId.set(c.driveFileId, {
      ...older,
      ...newer,
      status: newer.status ?? older.status,
      progressPercent: newer.progressPercent ?? older.progressPercent,
    })
  }
  return [...byId.values()]
}

const sidecar = makeSidecar<ReadingChange[]>({
  filename: FILENAME,
  cacheKey: 'bookbrain.readingPending',
  empty: [],
  parse,
  serialise,
  merge: mergeQueues,
})

// load → merge into any earlier change for this book → save (with the
// concurrency guard from makeSidecar).
export async function queueReadingChange(
  token: string,
  libraryFolderId: string,
  change: ReadingChange,
): Promise<void> {
  await sidecar.write(token, libraryFolderId, (all) => upsert(all, change))
}

// driveFileId → the pending status, for the optimistic overlay.
export async function loadPendingReading(
  token: string,
  libraryFolderId: string,
): Promise<Map<string, ReadingStatus>> {
  const changes = await sidecar.fetch(token, libraryFolderId)
  const out = new Map<string, ReadingStatus>()
  for (const c of changes) {
    if (c.status) out.set(c.driveFileId, c.status as ReadingStatus)
  }
  return out
}
