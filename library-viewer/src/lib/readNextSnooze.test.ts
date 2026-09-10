import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  activeSnoozes,
  cachedReadNextSnoozes,
  readNextSnoozesSynced,
  snoozeReadNext,
  SNOOZE_DAYS,
} from './readNextSnooze'

const driveMock = vi.hoisted(() => ({ readJsonFile: vi.fn(), writeJsonFile: vi.fn() }))
vi.mock('./drive', () => driveMock)

const DAY = 24 * 60 * 60 * 1000

beforeEach(() => {
  driveMock.readJsonFile.mockReset()
  driveMock.writeJsonFile.mockReset()
  driveMock.writeJsonFile.mockResolvedValue('snooze-file')
})

describe('activeSnoozes / expiry', () => {
  it('keeps snoozes younger than SNOOZE_DAYS, drops older ones', () => {
    const now = Date.now()
    const map = { fresh: now - 3 * DAY, old: now - (SNOOZE_DAYS + 1) * DAY }
    expect(activeSnoozes(map, now)).toEqual(new Set(['fresh']))
  })
})

describe('readNextSnoozesSynced', () => {
  it('merges the sidecar with the local cache, drops stale, writes the union', async () => {
    const now = Date.now()
    localStorage.setItem('bookbrain.readNextSnoozed', JSON.stringify({ local: now - DAY }))
    driveMock.readJsonFile.mockResolvedValue({
      id: 'snooze-file',
      modifiedTime: 't',
      content: { version: 1, snoozed: { server: now - 2 * DAY, ancient: now - 999 * DAY } },
    })
    const out = await readNextSnoozesSynced('tok', 'lib')
    expect(new Set(Object.keys(out))).toEqual(new Set(['local', 'server']))
    // wrote the merged+pruned map back (it differed from the server copy)
    expect(driveMock.writeJsonFile).toHaveBeenCalled()
    expect(new Set(Object.keys(cachedReadNextSnoozes()))).toEqual(new Set(['local', 'server']))
  })

  it('falls back to the (pruned) cache when Drive fails', async () => {
    const now = Date.now()
    localStorage.setItem(
      'bookbrain.readNextSnoozed',
      JSON.stringify({ a: now - DAY, b: now - 999 * DAY }),
    )
    driveMock.readJsonFile.mockRejectedValue(new Error('offline'))
    expect(Object.keys(await readNextSnoozesSynced('tok', 'lib'))).toEqual(['a'])
  })
})

describe('snoozeReadNext', () => {
  it('adds a card and merges with the server copy', async () => {
    const now = Date.now()
    driveMock.readJsonFile.mockResolvedValue({
      id: 'snooze-file',
      modifiedTime: 't',
      content: { version: 1, snoozed: { fromOtherDevice: now - DAY } },
    })
    const out = await snoozeReadNext('tok', 'lib', 'newCard', {})
    expect(new Set(Object.keys(out))).toEqual(new Set(['fromOtherDevice', 'newCard']))
    const body = driveMock.writeJsonFile.mock.calls.at(-1)![3] as { snoozed: Record<string, number> }
    expect('newCard' in body.snoozed).toBe(true)
  })
})
