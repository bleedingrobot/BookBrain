import { afterEach, describe, expect, it, vi } from 'vitest'
import { searchBooks } from './bookSearch'

function jsonResponse(body: unknown, ok = true, status = 200): Response {
  return { ok, status, json: async () => body } as Response
}

const GOOGLE = 'https://www.googleapis.com/books/v1/volumes'
const OPENLIB = 'https://openlibrary.org/search.json'

afterEach(() => vi.restoreAllMocks())

describe('searchBooks', () => {
  it('returns Google Books results when Google works', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      expect(String(input)).toContain(GOOGLE)
      return jsonResponse({
        items: [
          {
            volumeInfo: {
              title: 'Dune',
              authors: ['Frank Herbert'],
              publishedDate: '1965-08-01',
              industryIdentifiers: [{ type: 'ISBN_13', identifier: '9780441013593' }],
            },
          },
        ],
      })
    })

    const hits = await searchBooks('dune')
    expect(hits).toEqual([
      {
        title: 'Dune',
        author: 'Frank Herbert',
        series: null,
        isbn13: '9780441013593',
        cover: null,
        year: '1965',
      },
    ])
  })

  it('falls back to Open Library when Google keeps 429ing', async () => {
    const calls: string[] = []
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const u = String(input)
      calls.push(u)
      if (u.startsWith(GOOGLE)) return jsonResponse({}, false, 429)
      return jsonResponse({
        docs: [
          {
            title: 'The Halfmen of O',
            author_name: ['Maurice Gee'],
            first_publish_year: 1982,
            isbn: ['0195581601', '9780195581607'],
            cover_i: 123,
          },
        ],
      })
    })

    const hits = await searchBooks('halfmen of o')
    // two Google attempts (with a retry), then Open Library
    expect(calls.filter((c) => c.startsWith(GOOGLE))).toHaveLength(2)
    expect(calls.some((c) => c.startsWith(OPENLIB))).toBe(true)
    expect(hits[0]).toMatchObject({
      title: 'The Halfmen of O',
      author: 'Maurice Gee',
      isbn13: '9780195581607',
      cover: 'https://covers.openlibrary.org/b/id/123-M.jpg',
      year: '1982',
    })
  })

  it('throws a friendly error only when both sources fail', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse({}, false, 500))
    await expect(searchBooks('x')).rejects.toThrow(/unavailable right now/)
  })

  it('does not send a key param when none is configured', async () => {
    let googleUrl = ''
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      googleUrl = String(input)
      return jsonResponse({ items: [] })
    })
    await searchBooks('dune')
    expect(googleUrl).toContain(GOOGLE)
    expect(googleUrl).not.toContain('key=')
  })
})
