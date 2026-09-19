import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  cachedDismissedNews,
  dismissedNewsSynced,
  dismissNewsItem,
  normaliseNews,
  pruneDismissedNews,
  timeAgo,
} from './news'

const store = vi.hoisted(() => ({ file: null as { content: unknown; modifiedTime: string } | null, writes: 0 }))
const driveMock = vi.hoisted(() => ({ readJsonFile: vi.fn(), writeJsonFile: vi.fn() }))
vi.mock('./drive', () => driveMock)

function installDefaults() {
  driveMock.readJsonFile.mockReset()
  driveMock.writeJsonFile.mockReset()
  driveMock.readJsonFile.mockImplementation(async () =>
    store.file ? { id: 'dismissed-file', modifiedTime: store.file.modifiedTime, content: store.file.content } : null,
  )
  driveMock.writeJsonFile.mockImplementation(async (_t, _f, _n, content: unknown) => {
    store.writes += 1
    store.file = { content, modifiedTime: `t${store.writes}` }
    return 'dismissed-file'
  })
}

function seedDismissed(links: string[]) {
  store.file = { content: { version: 1, links }, modifiedTime: 't0' }
}

beforeEach(() => {
  store.file = null
  store.writes = 0
  installDefaults()
  localStorage.clear()
})

describe('normaliseNews', () => {
  it('keeps valid items and drops the junk', () => {
    const out = normaliseNews({
      version: 1,
      generatedAt: '2026-09-10T00:00:00Z',
      items: [
        {
          title: 'Cover reveal',
          link: 'https://reactormag.com/x',
          summary: 'A nice cover.',
          published: '2026-09-09T12:00:00Z',
          source: 'Reactor',
        },
        { title: 'No link', summary: 'x' },
        { title: '  ', link: 'https://x.example/y' }, // blank title
        { title: 'Bad scheme', link: 'javascript:alert(1)' },
        { title: 'Missing fields', link: 'https://locusmag.com/z' },
      ],
    })
    expect(out.generatedAt).toBe('2026-09-10T00:00:00Z')
    expect(out.items.map((i) => i.title)).toEqual(['Cover reveal', 'Missing fields'])
    expect(out.items[1]).toEqual({
      title: 'Missing fields',
      link: 'https://locusmag.com/z',
      summary: '',
      published: null,
      source: 'SFF news',
    })
  })

  it('tolerates a missing items array', () => {
    expect(normaliseNews({}).items).toEqual([])
  })
})

describe('dismissed articles (Drive-synced)', () => {
  it('reads the sidecar, caches it, and falls back to the cache on failure', async () => {
    seedDismissed(['https://x.example/a', 5 as unknown as string, 'https://x.example/b'])
    const d = await dismissedNewsSynced('tok', 'lib')
    expect(d).toEqual(new Set(['https://x.example/a', 'https://x.example/b']))
    expect(cachedDismissedNews()).toEqual(d)

    driveMock.readJsonFile.mockRejectedValue(new Error('offline'))
    expect(await dismissedNewsSynced('tok', 'lib')).toEqual(d) // from cache
  })

  it('merges a dismissal with the sidecar and the caller set, and writes it back', async () => {
    seedDismissed(['https://from-other-device'])
    const d = await dismissNewsItem('tok', 'lib', 'https://new', new Set(['https://local']))
    expect(d).toEqual(new Set(['https://from-other-device', 'https://local', 'https://new']))
    const body = store.file!.content as { links: string[] }
    expect(new Set(body.links)).toEqual(d)
  })

  it('prunes links no longer in any feed and writes the smaller set', async () => {
    seedDismissed(['https://x.example/here', 'https://x.example/gone'])
    const current = new Set(['https://x.example/here', 'https://x.example/gone'])
    const next = await pruneDismissedNews('tok', 'lib', current, [
      'https://x.example/here',
      'https://x.example/other',
    ])
    expect(next).toEqual(new Set(['https://x.example/here']))
    expect((store.file!.content as { links: string[] }).links).toEqual(['https://x.example/here'])
  })

  it('prune is a no-op (no write) when nothing dropped', async () => {
    seedDismissed(['https://x.example/here'])
    store.writes = 0
    const current = new Set(['https://x.example/here'])
    const next = await pruneDismissedNews('tok', 'lib', current, ['https://x.example/here'])
    expect(next).toBe(current)
    expect(store.writes).toBe(0)
  })
})

describe('timeAgo', () => {
  it('formats recent times and falls back to a date for old ones', () => {
    const now = Date.now()
    expect(timeAgo(new Date(now - 5 * 60_000).toISOString())).toBe('5m ago')
    expect(timeAgo(new Date(now - 3 * 3_600_000).toISOString())).toBe('3h ago')
    expect(timeAgo(new Date(now - 2 * 86_400_000).toISOString())).toBe('2d ago')
    expect(timeAgo(null)).toBe('')
    expect(timeAgo('not a date')).toBe('')
    // older than 2 weeks → a locale date string, not "Nd ago"
    expect(timeAgo(new Date(now - 40 * 86_400_000).toISOString())).not.toMatch(/ago/)
  })
})
