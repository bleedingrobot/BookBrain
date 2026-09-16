// Natural-language ("meaning") search. The book vectors are pre-computed by
// the backend (embeddings.ts loads them); here we load the *same* model once,
// embed only the query string, and rank by cosine similarity — a plain int8
// dot-product over ~2.4k rows, which is instant.
//
// The model is vendored under public/models/ and the ONNX-runtime WASM is
// bundled by Vite from @huggingface/transformers — both same-origin, so the
// service worker caches them and search works offline after the first use.

import type { Embeddings } from './embeddings'

// all-MiniLM-L6-v2, quantized — the exact file the backend embeds with.
const MODEL_ID = 'all-MiniLM-L6-v2'

type Extractor = (
  text: string,
  opts: { pooling: 'mean'; normalize: boolean },
) => Promise<{ data: Float32Array }>

let extractorPromise: Promise<Extractor> | null = null
let loaded = false

export function isModelLoaded(): boolean {
  return loaded
}

// Kicks off (or awaits) the one-time model download + init. First call in a
// session downloads ~23 MB; cached by the browser/SW after that.
export async function ensureModel(): Promise<void> {
  if (!extractorPromise) {
    extractorPromise = (async () => {
      const { pipeline, env } = await import('@huggingface/transformers')
      const base = import.meta.env.BASE_URL
      env.allowRemoteModels = false // never phone home — the model is vendored
      env.allowLocalModels = true
      env.localModelPath = `${base}models/`
      // GitHub Pages can't send COOP/COEP headers, so no SharedArrayBuffer —
      // single-threaded WASM is the only option (and plenty for one query).
      const wasm = env.backends?.onnx?.wasm
      if (wasm) wasm.numThreads = 1
      const extractor = (await pipeline('feature-extraction', MODEL_ID, {
        dtype: 'q8',
      })) as unknown as Extractor
      loaded = true
      return extractor
    })()
  }
  await extractorPromise
}

function quantise(vec: Float32Array): Int8Array {
  const q = new Int8Array(vec.length)
  for (let i = 0; i < vec.length; i++) {
    q[i] = Math.max(-127, Math.min(127, Math.round(vec[i] * 127)))
  }
  return q
}

export async function embedQuery(query: string): Promise<Int8Array> {
  await ensureModel()
  const extractor = await extractorPromise!
  const out = await extractor(query, { pooling: 'mean', normalize: true })
  return quantise(out.data)
}

export interface ScoredHit {
  id: string
  score: number // ~cosine, 0..1
}

// Rank every book vector against the (already int8) query. Pure — unit-tested.
export function rank(query: Int8Array, emb: Embeddings, topK = 80): ScoredHit[] {
  const { ids, vectors, dim } = emb
  const out: ScoredHit[] = new Array(ids.length)
  for (let r = 0; r < ids.length; r++) {
    let dot = 0
    const base = r * dim
    for (let k = 0; k < dim; k++) dot += query[k] * vectors[base + k]
    out[r] = { id: ids[r], score: dot / (127 * 127) }
  }
  out.sort((a, b) => b.score - a.score)
  return out.slice(0, topK)
}

export async function semanticSearch(
  query: string,
  emb: Embeddings,
  topK = 80,
): Promise<ScoredHit[]> {
  return rank(await embedQuery(query), emb, topK)
}
