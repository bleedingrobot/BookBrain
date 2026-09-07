import { describe, expect, it } from 'vitest'
import { evictionPlan, type CacheEntryMeta } from './bookCache'

const e = (fileId: string, bytes: number, lastUsedAt: number): CacheEntryMeta => ({
  fileId,
  bytes,
  lastUsedAt,
})

describe('evictionPlan', () => {
  it('keeps everything when under both limits', () => {
    const entries = [e('a', 10, 1), e('b', 10, 2)]
    expect(evictionPlan(entries, { maxBytes: 100, maxCount: 10 })).toEqual([])
  })

  it('drops least-recently-used first to fit the byte cap', () => {
    const entries = [e('old', 60, 1), e('mid', 60, 2), e('new', 60, 3)]
    expect(evictionPlan(entries, { maxBytes: 130, maxCount: 10 })).toEqual(['old'])
  })

  it('drops to fit the count cap', () => {
    const entries = [e('a', 1, 1), e('b', 1, 2), e('c', 1, 3), e('d', 1, 4)]
    expect(evictionPlan(entries, { maxBytes: 1e9, maxCount: 2 })).toEqual(['a', 'b'])
  })

  it('never evicts the incoming book even if it is the oldest', () => {
    const entries = [e('incoming', 200, 0), e('other', 200, 5)]
    expect(evictionPlan(entries, { maxBytes: 300, maxCount: 10, incoming: 'incoming' })).toEqual([
      'other',
    ])
  })

  it('handles an empty cache', () => {
    expect(evictionPlan([], { maxBytes: 10, maxCount: 1 })).toEqual([])
  })
})
