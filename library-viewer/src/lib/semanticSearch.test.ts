import { describe, expect, it } from 'vitest'
import type { Embeddings } from './embeddings'
import { rank } from './semanticSearch'

// Three 2-D unit-ish vectors (int8 scale), so dot-products are easy to reason
// about: "east", "north", "north-east".
const emb: Embeddings = {
  model: 'test',
  dim: 2,
  ids: ['east', 'north', 'ne'],
  vectors: new Int8Array([127, 0, 0, 127, 90, 90]),
}

describe('rank', () => {
  it('orders by cosine to the query, best first', () => {
    const east = new Int8Array([127, 0])
    const out = rank(east, emb)
    expect(out.map((h) => h.id)).toEqual(['east', 'ne', 'north'])
    expect(out[0].score).toBeCloseTo(1, 2)
    expect(out[2].score).toBeCloseTo(0, 2)
  })

  it('a diagonal query puts the diagonal vector on top', () => {
    const out = rank(new Int8Array([90, 90]), emb)
    expect(out[0].id).toBe('ne')
  })

  it('respects topK', () => {
    expect(rank(new Int8Array([127, 0]), emb, 2)).toHaveLength(2)
  })
})
