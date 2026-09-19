import { describe, expect, it } from 'vitest'
import { normalisePrompts } from './prompts'

describe('normalisePrompts', () => {
  it('keeps questions with 2+ owned books, drops the rest', () => {
    const out = normalisePrompts({
      version: 1,
      generatedAt: '2026-09-10T00:00:00Z',
      prompts: [
        { question: 'Two books', slug: 'two', driveIds: ['a', 'b', 'c'] },
        { question: 'One book', slug: 'one', driveIds: ['a'] },
        { question: '  ', driveIds: ['a', 'b'] }, // blank question
        { question: 'Bad ids', driveIds: ['a', 5 as unknown as string] }, // → 1 valid → dropped
      ],
    })
    expect(out.generatedAt).toBe('2026-09-10T00:00:00Z')
    expect(out.prompts).toEqual([{ question: 'Two books', slug: 'two', driveIds: ['a', 'b', 'c'] }])
  })

  it('tolerates a missing prompts array', () => {
    expect(normalisePrompts({}).prompts).toEqual([])
  })
})
