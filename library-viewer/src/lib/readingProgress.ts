// Where you are in each book. Per-device (localStorage) for now — prompts/17
// §E moves the backing store to a Drive sidecar so position syncs across a
// person's devices. Keep the getProgress / setProgress / allProgress surface
// stable so that swap doesn't touch the Reader.

const KEY = 'bookbrain.readingProgress'

// A book at or past this fraction counts as finished — it drops off the
// "Continue reading" strip. 1.0 is never quite reached (last page is a range).
export const FINISHED_FRACTION = 0.98

export interface BookProgress {
  cfi: string
  percent: number // 0..1
  updatedAt: number
}

export type ProgressMap = Record<string, BookProgress>

export function allProgress(): ProgressMap {
  try {
    const raw = localStorage.getItem(KEY)
    if (!raw) return {}
    const parsed = JSON.parse(raw) as unknown
    if (!parsed || typeof parsed !== 'object') return {}
    const out: ProgressMap = {}
    for (const [id, value] of Object.entries(parsed as Record<string, unknown>)) {
      const v = value as Partial<BookProgress>
      if (typeof v?.cfi === 'string' && typeof v.percent === 'number') {
        out[id] = {
          cfi: v.cfi,
          percent: Math.max(0, Math.min(1, v.percent)),
          updatedAt: typeof v.updatedAt === 'number' ? v.updatedAt : 0,
        }
      }
    }
    return out
  } catch {
    return {}
  }
}

export function getProgress(fileId: string): BookProgress | null {
  return allProgress()[fileId] ?? null
}

export function setProgress(fileId: string, cfi: string, percent: number): void {
  try {
    const map = allProgress()
    map[fileId] = { cfi, percent: Math.max(0, Math.min(1, percent)), updatedAt: Date.now() }
    localStorage.setItem(KEY, JSON.stringify(map))
  } catch {
    // storage full / disabled — reading still works, position just won't stick
  }
}

export function clearProgress(fileId: string): void {
  try {
    const map = allProgress()
    delete map[fileId]
    localStorage.setItem(KEY, JSON.stringify(map))
  } catch {
    // ignore
  }
}

/**
 * The "Continue reading" list: books with saved progress that are started but
 * not finished, most-recently-read first, capped. `knownFileIds` drops
 * entries whose file has left the library.
 */
export function continueReadingIds(
  map: ProgressMap,
  knownFileIds: Set<string>,
  limit = 12,
): string[] {
  return Object.entries(map)
    .filter(
      ([id, p]) =>
        knownFileIds.has(id) && p.percent > 0 && p.percent < FINISHED_FRACTION,
    )
    .sort(([, a], [, b]) => b.updatedAt - a.updatedAt)
    .slice(0, limit)
    .map(([id]) => id)
}
