import { describe, expect, it } from 'vitest'
import { normaliseLists } from './lists'

describe('normaliseLists', () => {
  it('keeps valid lists and candidates, drops junk', () => {
    const out = normaliseLists({
      version: 1,
      generatedAt: '2026-09-10T00:00:00Z',
      lists: [
        { name: 'Best SFF', slug: 'sff', owned: 12, total: 100 },
        { name: '   ', owned: 1, total: 2 }, // blank name
      ],
      candidates: [
        { title: 'A Book', author: 'A', isbn13: '9990000000001', fromList: 'Best SFF' },
        { title: '', fromList: 'Best SFF' }, // blank title
        { title: 'No author', author: 5 as unknown as string, isbn13: null, fromList: 'Best SFF' },
      ],
    })
    expect(out.lists).toEqual([{ name: 'Best SFF', slug: 'sff', owned: 12, total: 100 }])
    expect(out.candidates).toEqual([
      { title: 'A Book', author: 'A', isbn13: '9990000000001', fromList: 'Best SFF' },
      { title: 'No author', author: null, isbn13: null, fromList: 'Best SFF' },
    ])
  })

  it('tolerates missing arrays', () => {
    expect(normaliseLists({})).toEqual({ generatedAt: null, lists: [], candidates: [] })
  })
})
