import { beforeEach, describe, expect, it, vi } from 'vitest'
import { makeSidecar, type SidecarSpec } from './syncedSidecar'

const store = vi.hoisted(() => ({ file: null as { content: unknown; modifiedTime: string } | null, writes: 0 }))
const driveMock = vi.hoisted(() => ({ readJsonFile: vi.fn(), writeJsonFile: vi.fn() }))
vi.mock('./drive', () => driveMock)

function installDefaults() {
  driveMock.readJsonFile.mockReset()
  driveMock.writeJsonFile.mockReset()
  driveMock.readJsonFile.mockImplementation(async () =>
    store.file ? { id: 'f', modifiedTime: store.file.modifiedTime, content: store.file.content } : null,
  )
  driveMock.writeJsonFile.mockImplementation(async (_t, _f, _n, content: unknown) => {
    store.writes += 1
    store.file = { content, modifiedTime: `t${store.writes}` }
    return 'f'
  })
}

// A set-of-strings sidecar (like news-dismissed / readnext-snoozed).
const spec: SidecarSpec<string[]> = {
  filename: 'x.json',
  cacheKey: 'bookbrain.x',
  empty: [],
  parse: (raw) => {
    const arr = (raw as { items?: unknown })?.items
    return Array.isArray(arr) ? arr.filter((s): s is string => typeof s === 'string') : []
  },
  serialise: (v) => ({ version: 1, items: [...v].sort() }),
  merge: (a, b) => [...new Set([...a, ...b])],
}

beforeEach(() => {
  store.file = null
  store.writes = 0
  installDefaults()
  localStorage.clear()
})

describe('makeSidecar', () => {
  it('write creates the file then updates it', async () => {
    const s = makeSidecar(spec)
    await s.write('t', 'lib', (cur) => [...cur, 'a'])
    expect((store.file!.content as { items: string[] }).items).toEqual(['a'])
    await s.write('t', 'lib', (cur) => [...cur, 'b'])
    expect((store.file!.content as { items: string[] }).items).toEqual(['a', 'b'])
  })

  it('serves a modifiedTime-matched fetch from cache without a download', async () => {
    const s = makeSidecar(spec)
    await s.write('t', 'lib', () => ['a'])
    driveMock.readJsonFile.mockImplementationOnce(async () => ({
      id: 'f',
      modifiedTime: store.file!.modifiedTime, // unchanged
      content: { should: 'not be parsed' },
    }))
    expect(await s.fetch('t', 'lib')).toEqual(['a']) // from cache, not the bogus content
  })

  it('reconciles a concurrent clobbering write', async () => {
    const s = makeSidecar(spec)
    let writes = 0
    driveMock.writeJsonFile.mockImplementation(async (_t, _f, _n, content: unknown) => {
      writes += 1
      store.file = { content, modifiedTime: `t${writes}` }
      if (writes === 1) {
        // a sibling overwrites with a disjoint set right after our PATCH
        store.file = { content: { version: 1, items: ['sibling'] }, modifiedTime: 'sib' }
      }
      return 'f'
    })
    const result = await s.write('t', 'lib', () => ['mine'])
    expect(new Set(result)).toEqual(new Set(['mine', 'sibling']))
    expect(new Set((store.file!.content as { items: string[] }).items)).toEqual(
      new Set(['mine', 'sibling']),
    )
    expect(writes).toBe(2) // initial + one reconcile
  })

  it('serialises two writes to the same file in order', async () => {
    const s = makeSidecar(spec)
    const order: string[] = []
    driveMock.writeJsonFile.mockImplementation(async (_t, _f, _n, content: unknown) => {
      order.push((content as { items: string[] }).items.join(','))
      store.writes += 1
      store.file = { content, modifiedTime: `t${store.writes}` }
      return 'f'
    })
    await Promise.all([
      s.write('t', 'lib', (cur) => [...cur, 'a']),
      s.write('t', 'lib', (cur) => [...cur, 'b']),
    ])
    // second write saw the first's result
    expect((store.file!.content as { items: string[] }).items.sort()).toEqual(['a', 'b'])
  })

  it('a failed initial read keeps prior state and applies the change on top', async () => {
    const s = makeSidecar(spec)
    await s.write('t', 'lib', () => ['a', 'b']) // now on Drive + cache
    store.writes = 0
    // Drive read blows up (transient 403 / offline) on the next write's read.
    driveMock.readJsonFile.mockRejectedValueOnce(new Error('rate limit'))
    const out = await s.write('t', 'lib', (cur) => [...cur, 'c'])
    expect(new Set(out)).toEqual(new Set(['a', 'b', 'c'])) // not collapsed to ['c'] or []
    expect(store.writes).toBe(0) // no blind overwrite of Drive
    // the change survives locally for the next sync to carry up
    const s2 = makeSidecar(spec)
    expect(new Set(s2.cachedNow())).toEqual(new Set(['a', 'b', 'c']))
  })

  it('sync seeds the sidecar from a localStorage-only install', async () => {
    localStorage.setItem(
      'bookbrain.x',
      JSON.stringify({ folderId: 'lib', modifiedTime: null, value: { items: ['local-only'] } }),
    )
    const s = makeSidecar(spec)
    const out = await s.sync('t', 'lib')
    expect(out).toEqual(['local-only'])
    expect((store.file!.content as { items: string[] }).items).toEqual(['local-only'])
  })
})
