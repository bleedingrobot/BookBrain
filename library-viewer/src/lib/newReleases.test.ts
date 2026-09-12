import { afterEach, describe, expect, it, vi } from 'vitest'
import { clearNewReleasesCache, fetchNewReleases, normaliseNewReleases } from './newReleases'

afterEach(() => {
  vi.restoreAllMocks()
  clearNewReleasesCache()
})

describe('normaliseNewReleases', () => {
  it('maps raw items to ReleaseItems and drops title-less junk', () => {
    const out = normaliseNewReleases({
      version: 1,
      recent: [
        {
          title: 'Onyx Storm',
          author: 'Rebecca Yarros',
          isbn13: '9781649374189',
          releaseDate: '2025-01-21',
          genres: ['Fantasy', 'Romance'],
        },
        { author: 'no title' },
      ],
      upcoming: [{ title: 'Threshing Day', author: null, releaseDate: '2026-09-29' }],
    })
    expect(out.recent).toEqual([
      {
        key: '9781649374189',
        title: 'Onyx Storm',
        author: 'Rebecca Yarros',
        series: null,
        seriesPosition: null,
        isbn13: '9781649374189',
        releaseDate: '2025-01-21',
        description: null,
        hardcoverSlug: null,
        genres: ['Fantasy', 'Romance'],
        source: 'author',
      },
    ])
    expect(out.upcoming[0].key).toBe('author:threshing day|')
    expect(out.upcoming[0].isbn13).toBeNull()
  })
})

function jsonResponse(body: unknown, ok = true): Response {
  return { ok, status: ok ? 200 : 500, json: async () => body } as Response
}

describe('fetchNewReleases', () => {
  it('downloads, normalises, and caches; a matching modifiedTime skips the re-download', async () => {
    const payload = {
      version: 1,
      recent: [{ title: 'A', author: 'X', releaseDate: '2025-01-01' }],
      upcoming: [],
    }
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const u = String(input)
      if (u.includes('files?q=')) return jsonResponse({ files: [{ id: 'f1', modifiedTime: 't1' }] })
      return jsonResponse(payload)
    })

    const first = await fetchNewReleases('tok', 'lib-1')
    expect(first.recent.map((i) => i.title)).toEqual(['A'])
    expect(fetchMock).toHaveBeenCalledTimes(2)

    const second = await fetchNewReleases('tok', 'lib-1')
    expect(second).toEqual(first)
    expect(fetchMock).toHaveBeenCalledTimes(3) // list check only
  })

  it('returns empty on error or a missing file', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse({ files: [] }))
    expect(await fetchNewReleases('tok', 'lib-1')).toEqual({
      recent: [],
      upcoming: [],
      global: [],
      trending: [],
    })
  })
})
