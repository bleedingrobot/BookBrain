import { describe, expect, it } from 'vitest'
import { decodeEmbeddings } from './embeddings'

function encode(model: string, dim: number, ids: string[], rows: number[][]): ArrayBuffer {
  const header = JSON.stringify({ version: 1, model, dim, count: ids.length, ids })
  const headerBytes = new TextEncoder().encode(header)
  const block = new Int8Array(ids.length * dim)
  rows.forEach((r, i) => r.forEach((v, k) => (block[i * dim + k] = v)))
  const buf = new Uint8Array(4 + headerBytes.length + block.length)
  new DataView(buf.buffer).setUint32(0, headerBytes.length, true)
  buf.set(headerBytes, 4)
  buf.set(new Uint8Array(block.buffer), 4 + headerBytes.length)
  return buf.buffer
}

describe('decodeEmbeddings', () => {
  it('parses the header and the int8 vector block', () => {
    const buf = encode('all-MiniLM-L6-v2', 3, ['a', 'b'], [
      [127, 0, -127],
      [1, 2, 3],
    ])
    const emb = decodeEmbeddings(buf)
    expect(emb.model).toBe('all-MiniLM-L6-v2')
    expect(emb.dim).toBe(3)
    expect(emb.ids).toEqual(['a', 'b'])
    expect(emb.vectors.length).toBe(6)
    expect([...emb.vectors]).toEqual([127, 0, -127, 1, 2, 3])
  })

  it('handles an empty library (count 0)', () => {
    const emb = decodeEmbeddings(encode('m', 4, [], []))
    expect(emb.ids).toEqual([])
    expect(emb.vectors.length).toBe(0)
  })
})
