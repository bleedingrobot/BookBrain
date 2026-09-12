import { beforeEach, describe, expect, it, vi } from 'vitest'
import { loadPendingReading, queueReadingChange, type ReadingChange } from './readingQueue'

// A tiny stateful fake of the one Drive JSON file this module touches, so the
// read-modify-write (and makeSidecar's re-read concurrency guard) behave the
// way they would against real Drive.
const store = vi.hoisted(() => ({ file: null as { content: unknown; modifiedTime: string } | null, writes: 0 }))

const driveMock = vi.hoisted(() => ({ readJsonFile: vi.fn(), writeJsonFile: vi.fn() }))
vi.mock('./drive', () => driveMock)

function installDefaults() {
  driveMock.readJsonFile.mockReset()
  driveMock.writeJsonFile.mockReset()
  driveMock.readJsonFile.mockImplementation(async () =>
    store.file ? { id: 'pending-file', modifiedTime: store.file.modifiedTime, content: store.file.content } : null,
  )
  driveMock.writeJsonFile.mockImplementation(async (_t: string, _f: string, _n: string, content: unknown) => {
    store.writes += 1
    store.file = { content, modifiedTime: `t${store.writes}` }
    return 'pending-file'
  })
}

const change = (over: Partial<ReadingChange> = {}): ReadingChange => ({
  driveFileId: 'file-1',
  isbn13: '9781234567890',
  title: 'A Book',
  author: 'An Author',
  status: 'read',
  at: '2026-09-09T00:00:00.000Z',
  by: 'James',
  ...over,
})

function seed(changes: ReadingChange[]) {
  store.file = { content: { version: 1, changes }, modifiedTime: 't0' }
}

beforeEach(() => {
  store.file = null
  store.writes = 0
  installDefaults()
})

describe('queueReadingChange', () => {
  it('appends to a fresh queue when none exists', async () => {
    await queueReadingChange('tok', 'lib', change())
    expect((store.file!.content as { changes: ReadingChange[] }).changes).toEqual([change()])
  })

  it('replaces an earlier queued change for the same book', async () => {
    seed([change({ status: 'reading' })])
    await queueReadingChange('tok', 'lib', change({ status: 'read' }))
    const changes = (store.file!.content as { changes: ReadingChange[] }).changes
    expect(changes).toHaveLength(1)
    expect(changes[0].status).toBe('read')
  })

  it('merges a progress push into an unsynced status change (Part I)', async () => {
    seed([change({ status: 'reading' })])
    await queueReadingChange('tok', 'lib', {
      driveFileId: 'file-1',
      isbn13: null,
      title: 'A Book',
      author: null,
      progressPercent: 0.4,
      at: '2026-09-10T00:00:00.000Z',
      by: 'James',
    })
    const changes = (store.file!.content as { changes: ReadingChange[] }).changes
    expect(changes).toHaveLength(1)
    expect(changes[0].status).toBe('reading') // not lost
    expect(changes[0].progressPercent).toBe(0.4)
  })

  it('keeps queued changes for other books', async () => {
    seed([change({ driveFileId: 'file-2', status: 'want' })])
    await queueReadingChange('tok', 'lib', change({ driveFileId: 'file-1' }))
    const ids = (store.file!.content as { changes: ReadingChange[] }).changes.map((c) => c.driveFileId)
    expect(ids.sort()).toEqual(['file-1', 'file-2'])
  })

  it('recovers our change when a sibling write clobbers it right after ours', async () => {
    // We PATCH [file-1]. A sibling then PATCHes [file-2] (a full overwrite —
    // our file-1 is gone from disk). makeSidecar's post-write re-read + merge
    // must notice and write [file-1, file-2].
    let writes = 0
    driveMock.writeJsonFile.mockImplementation(async (_t, _f, _n, content: unknown) => {
      writes += 1
      store.file = { content, modifiedTime: `t${writes}` }
      if (writes === 1) {
        store.file = {
          content: { version: 1, changes: [change({ driveFileId: 'file-2', status: 'want' })] },
          modifiedTime: 'sibling',
        }
      }
      return 'pending-file'
    })

    await queueReadingChange('tok', 'lib', change({ driveFileId: 'file-1' }))

    const ids = (store.file!.content as { changes: ReadingChange[] }).changes.map((c) => c.driveFileId)
    expect(ids.sort()).toEqual(['file-1', 'file-2'])
    expect(writes).toBe(2) // the reconcile write put file-1 back
  })
})

describe('loadPendingReading', () => {
  it('returns an empty map when there is no file', async () => {
    expect((await loadPendingReading('tok', 'lib')).size).toBe(0)
  })

  it('maps drive file id to the queued status', async () => {
    seed([change({ driveFileId: 'a', status: 'read' }), change({ driveFileId: 'b', status: 'dnf' })])
    const map = await loadPendingReading('tok', 'lib')
    expect(map.get('a')).toBe('read')
    expect(map.get('b')).toBe('dnf')
  })

  it('swallows errors and returns an empty map', async () => {
    driveMock.readJsonFile.mockRejectedValueOnce(new Error('boom'))
    expect((await loadPendingReading('tok', 'lib')).size).toBe(0)
  })
})
