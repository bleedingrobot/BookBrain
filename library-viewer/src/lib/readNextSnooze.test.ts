import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  activeSnoozes,
  cachedReadNextSnoozes,
  readNextSnoozesSynced,
  snoozeReadNext,
  SNOOZE_DAYS,
  type SnoozeMap,
} from './readNextSnooze'

const store = vi.hoisted(() => ({ file: null as { content: unknown; modifiedTime: string } | null, writes: 0 }))
const driveMock = vi.hoisted(() => ({ readJsonFile: vi.fn(), writeJsonFile: vi.fn() }))
vi.mock('./drive', () => driveMock)

const DAY = 24 * 60 * 60 * 1000

function installDefaults() {
  driveMock.readJsonFile.mockReset()
  driveMock.writeJsonFile.mockReset()
  driveMock.readJsonFile.mockImplementation(async () =>
    store.file ? { id: 's', modifiedTime: store.file.modifiedTime, content: store.file.content } : null,
  )
  driveMock.writeJsonFile.mockImplementation(async (_t, _f, _n, content: unknown) => {
    store.writes += 1
    store.file = { content, modifiedTime: `t${store.writes}` }
    return 's'
  })
}

function seedServer(snoozed: SnoozeMap) {
  store.file = { content: { version: 1, snoozed }, modifiedTime: 't0' }
}
function seedCache(map: SnoozeMap) {
  localStorage.setItem(
    'bookbrain.readNextSnoozed',
    JSON.stringify({ folderId: 'lib', modifiedTime: null, value: { version: 1, snoozed: map } }),
  )
}

beforeEach(() => {
  store.file = null
  store.writes = 0
  installDefaults()
  localStorage.clear()
})

describe('activeSnoozes / expiry', () => {
  it('keeps snoozes younger than SNOOZE_DAYS, drops older ones', () => {
    const now = Date.now()
    expect(
      activeSnoozes({ fresh: now - 3 * DAY, old: now - (SNOOZE_DAYS + 1) * DAY }, now),
    ).toEqual(new Set(['fresh']))
  })
})

describe('readNextSnoozesSynced', () => {
  it('merges the sidecar with the local cache, drops stale, writes the union', async () => {
    const now = Date.now()
    seedCache({ local: now - DAY })
    seedServer({ server: now - 2 * DAY, ancient: now - 999 * DAY })

    const out = await readNextSnoozesSynced('tok', 'lib')
    expect(new Set(Object.keys(out))).toEqual(new Set(['local', 'server']))
    expect(driveMock.writeJsonFile).toHaveBeenCalled()
    expect(new Set(Object.keys(cachedReadNextSnoozes()))).toEqual(new Set(['local', 'server']))
  })

  it('falls back to the (pruned) cache when Drive fails', async () => {
    const now = Date.now()
    seedCache({ a: now - DAY, b: now - 999 * DAY })
    driveMock.readJsonFile.mockRejectedValue(new Error('offline'))
    expect(Object.keys(await readNextSnoozesSynced('tok', 'lib'))).toEqual(['a'])
  })
})

describe('snoozeReadNext', () => {
  it('adds a card and merges with the server copy', async () => {
    const now = Date.now()
    seedServer({ fromOtherDevice: now - DAY })
    const out = await snoozeReadNext('tok', 'lib', 'newCard', {})
    expect(new Set(Object.keys(out))).toEqual(new Set(['fromOtherDevice', 'newCard']))
    const body = driveMock.writeJsonFile.mock.calls.at(-1)![3] as { snoozed: Record<string, number> }
    expect('newCard' in body.snoozed).toBe(true)
  })
})
