import { describe, expect, it } from 'vitest'
import {
  dismissNewsItem,
  loadDismissedNews,
  normaliseNews,
  pruneDismissedNews,
  timeAgo,
} from './news'

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

describe('dismissed articles', () => {
  it('persists a dismissal and reloads it', () => {
    let d = loadDismissedNews()
    expect(d.size).toBe(0)
    d = dismissNewsItem('https://x.example/a', d)
    d = dismissNewsItem('https://x.example/b', d)
    expect(loadDismissedNews()).toEqual(new Set(['https://x.example/a', 'https://x.example/b']))
  })

  it('prunes links no longer in any feed', () => {
    let d = dismissNewsItem('https://x.example/gone', dismissNewsItem('https://x.example/here', new Set()))
    d = pruneDismissedNews(d, ['https://x.example/here', 'https://x.example/other'])
    expect(d).toEqual(new Set(['https://x.example/here']))
    expect(loadDismissedNews()).toEqual(new Set(['https://x.example/here']))
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
