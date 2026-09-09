import { beforeEach, describe, expect, it, vi } from 'vitest'
import { loadPendingReading, queueReadingChange, type ReadingChange } from './readingQueue'

const driveMock = vi.hoisted(() => ({ readJsonFile: vi.fn(), writeJsonFile: vi.fn() }))
vi.mock('./drive', () => driveMock)

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

beforeEach(() => {
  driveMock.readJsonFile.mockReset()
  driveMock.writeJsonFile.mockReset()
  driveMock.writeJsonFile.mockResolvedValue('pending-file')
})

describe('queueReadingChange', () => {
  it('appends to a fresh queue when none exists', async () => {
    driveMock.readJsonFile.mockResolvedValue(null)
    await queueReadingChange('tok', 'lib', change())
    const [, , , body, id] = driveMock.writeJsonFile.mock.calls[0]
    expect(id).toBeNull()
    expect(body).toEqual({ version: 1, changes: [change()] })
  })

  it('replaces an earlier queued change for the same book', async () => {
    driveMock.readJsonFile.mockResolvedValue({
      id: 'pending-file',
      modifiedTime: 't',
      content: { version: 1, changes: [change({ status: 'reading' })] },
    })
    await queueReadingChange('tok', 'lib', change({ status: 'read' }))
    const body = driveMock.writeJsonFile.mock.calls[0][3]
    expect(body.changes).toHaveLength(1)
    expect(body.changes[0].status).toBe('read')
  })

  it('merges a progress push into an unsynced status change (Part I)', async () => {
    driveMock.readJsonFile.mockResolvedValue({
      id: 'pending-file',
      modifiedTime: 't',
      content: { version: 1, changes: [change({ status: 'reading' })] },
    })
    await queueReadingChange('tok', 'lib', {
      driveFileId: 'file-1',
      isbn13: null,
      title: 'A Book',
      author: null,
      progressPercent: 0.4,
      at: '2026-09-10T00:00:00.000Z',
      by: 'James',
    })
    const body = driveMock.writeJsonFile.mock.calls[0][3]
    expect(body.changes).toHaveLength(1)
    expect(body.changes[0].status).toBe('reading') // not lost
    expect(body.changes[0].progressPercent).toBe(0.4)
  })

  it('keeps queued changes for other books', async () => {
    driveMock.readJsonFile.mockResolvedValue({
      id: 'pending-file',
      modifiedTime: 't',
      content: { version: 1, changes: [change({ driveFileId: 'file-2', status: 'want' })] },
    })
    await queueReadingChange('tok', 'lib', change({ driveFileId: 'file-1' }))
    const body = driveMock.writeJsonFile.mock.calls[0][3]
    expect(body.changes.map((c: ReadingChange) => c.driveFileId).sort()).toEqual(['file-1', 'file-2'])
  })
})

describe('loadPendingReading', () => {
  it('returns an empty map when there is no file', async () => {
    driveMock.readJsonFile.mockResolvedValue(null)
    expect((await loadPendingReading('tok', 'lib')).size).toBe(0)
  })

  it('maps drive file id to the queued status', async () => {
    driveMock.readJsonFile.mockResolvedValue({
      id: 'pending-file',
      modifiedTime: 't',
      content: {
        version: 1,
        changes: [change({ driveFileId: 'a', status: 'read' }), change({ driveFileId: 'b', status: 'dnf' })],
      },
    })
    const map = await loadPendingReading('tok', 'lib')
    expect(map.get('a')).toBe('read')
    expect(map.get('b')).toBe('dnf')
  })

  it('swallows errors and returns an empty map', async () => {
    driveMock.readJsonFile.mockRejectedValue(new Error('boom'))
    expect((await loadPendingReading('tok', 'lib')).size).toBe(0)
  })
})
