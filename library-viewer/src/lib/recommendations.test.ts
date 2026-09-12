import { afterEach, describe, expect, it, vi } from 'vitest'
import { clearRecommendationsCache, fetchRecommendations, normaliseRecs } from './recommendations'

afterEach(() => {
  vi.restoreAllMocks()
  clearRecommendationsCache()
})

describe('normaliseRecs', () => {
  it('keeps well-formed entries and drops junk', () => {
    const out = normaliseRecs({
      books: {
        a: [
          { title: 'Red Rising', author: 'Pierce Brown', isbn13: '9780553588484' },
          { author: 'no title' } as { author: string },
          { title: 'The Poppy War', isbn13: 5 as unknown as string },
        ],
        b: [],
        c: 'nope' as unknown as [],
      },
    })
    expect(Object.keys(out)).toEqual(['a'])
    expect(out.a).toEqual([
      { title: 'Red Rising', author: 'Pierce Brown', isbn13: '9780553588484' },
      { title: 'The Poppy War', author: null, isbn13: null },
    ])
  })
})

function jsonResponse(body: unknown, ok = true): Response {
  return { ok, status: ok ? 200 : 500, json: async () => body } as Response
}

describe('fetchRecommendations', () => {
  it('downloads, normalises, and caches; a second call with the same modifiedTime skips the download', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockImplementation(async (input) => {
        const u = String(input)
        if (u.includes('files?q=')) return jsonResponse({ files: [{ id: 'f1', modifiedTime: 't1' }] })
        return jsonResponse({ version: 1, books: { d1: [{ title: 'Dune', author: 'Herbert' }] } })
      })

    const first = await fetchRecommendations('tok', 'lib-1')
    expect(first).toEqual({ d1: [{ title: 'Dune', author: 'Herbert', isbn13: null }] })
    expect(fetchMock).toHaveBeenCalledTimes(2) // list + download

    const second = await fetchRecommendations('tok', 'lib-1')
    expect(second).toEqual(first)
    expect(fetchMock).toHaveBeenCalledTimes(3) // just the list check, no re-download
  })

  it('returns {} (or the cache) on any error', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse({}, false))
    expect(await fetchRecommendations('tok', 'lib-1')).toEqual({})
  })

  it('returns {} when the file does not exist', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse({ files: [] }))
    expect(await fetchRecommendations('tok', 'lib-1')).toEqual({})
  })
})
