import { describe, expect, it } from 'vitest'
import { clearSentTracker, getSentMap, markSent, unmarkSent } from './sentTracker'

const FOLDER = 'folder-1'

describe('sentTracker', () => {
  it('starts empty', () => {
    expect(getSentMap()).toEqual({})
  })

  it('marks and reads back, keyed by folder + book', () => {
    markSent(FOLDER, ['a', 'b'])
    const map = getSentMap()
    expect(Object.keys(map[FOLDER])).toEqual(['a', 'b'])
    expect(typeof map[FOLDER].a).toBe('string') // ISO timestamp
  })

  it('markSent is additive and idempotent', () => {
    markSent(FOLDER, ['a'])
    markSent(FOLDER, ['a', 'c'])
    expect(Object.keys(getSentMap()[FOLDER]).sort()).toEqual(['a', 'c'])
  })

  it('unmarkSent drops only the named ids', () => {
    markSent(FOLDER, ['a', 'b', 'c'])
    unmarkSent(FOLDER, ['b'])
    expect(Object.keys(getSentMap()[FOLDER]).sort()).toEqual(['a', 'c'])
  })

  it('unmarkSent on an unknown folder is a no-op', () => {
    expect(() => unmarkSent('nope', ['x'])).not.toThrow()
  })

  it('markSent([]) does not create an empty bucket', () => {
    expect(markSent(FOLDER, [])).toEqual({})
  })

  it('clearSentTracker wipes everything', () => {
    markSent(FOLDER, ['a'])
    clearSentTracker()
    expect(getSentMap()).toEqual({})
  })

  it('survives a corrupt payload', () => {
    localStorage.setItem('bookbrain.sentToDevice', '{not json')
    expect(getSentMap()).toEqual({})
  })
})
