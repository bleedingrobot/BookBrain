import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fetchGoogleBooksCoverUrl } from './covers'

describe('fetchGoogleBooksCoverUrl', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('queries by ISBN when one is given, ignoring title/author', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        items: [{ volumeInfo: { imageLinks: { thumbnail: 'http://books.google.com/cover.jpg' } } }],
      }),
    })
    vi.stubGlobal('fetch', fetchMock)

    const url = await fetchGoogleBooksCoverUrl('9780441172719', 'Dune', 'Frank Herbert')

    expect(url).toBe('https://books.google.com/cover.jpg')
    const requested = new URL(fetchMock.mock.calls[0][0] as string)
    expect(requested.searchParams.get('q')).toBe('isbn:9780441172719')
  })

  it('falls back to a title+author query when there is no ISBN', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ items: [{ volumeInfo: { imageLinks: { smallThumbnail: 'https://x/cover.jpg' } } }] }),
    })
    vi.stubGlobal('fetch', fetchMock)

    const url = await fetchGoogleBooksCoverUrl(null, 'Dune', 'Frank Herbert')

    expect(url).toBe('https://x/cover.jpg')
    const requested = new URL(fetchMock.mock.calls[0][0] as string)
    expect(requested.searchParams.get('q')).toBe('intitle:Dune+inauthor:Frank Herbert')
  })

  it('caches by the same key instead of re-fetching', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ items: [{ volumeInfo: { imageLinks: { thumbnail: 'https://x/a.jpg' } } }] }),
    })
    vi.stubGlobal('fetch', fetchMock)

    await fetchGoogleBooksCoverUrl('1111111111111', 'Some Book', null)
    await fetchGoogleBooksCoverUrl('1111111111111', 'Some Book', null)

    expect(fetchMock).toHaveBeenCalledOnce()
  })

  it('returns null (and does not throw) when nothing comes back', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ items: [] }) }))

    expect(await fetchGoogleBooksCoverUrl('2222222222222', 'Nothing Found', null)).toBeNull()
  })

  it('returns null (and does not throw) on an HTTP error', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 503 }))

    expect(await fetchGoogleBooksCoverUrl('3333333333333', 'Whatever', null)).toBeNull()
  })

  it('returns null (and does not throw) when fetch itself rejects', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')))

    expect(await fetchGoogleBooksCoverUrl('4444444444444', 'Whatever', null)).toBeNull()
  })
})
