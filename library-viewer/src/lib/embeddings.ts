// bookbrain-embeddings.bin — one int8-quantised sentence embedding per
// organised book (prompts/29, written by embedding_service). A binary
// sidecar: a 4-byte little-endian header length, then a JSON header
// {version, model, dim, count, ids}, then count*dim int8 bytes row-major in
// `ids` order.
//
// ~1 MB for the whole library. Fetched from Drive when semantic search is
// first used, decoded once, and cached in IndexedDB (keyed by the file's
// modifiedTime) so a repeat visit — and offline — skips the download.

const FILENAME = 'bookbrain-embeddings.bin'
const DB_NAME = 'bookbrain-embeddings'
const STORE = 'sidecar'
const RECORD_KEY = 'current'

export interface Embeddings {
  model: string
  dim: number
  ids: string[]
  // length === ids.length * dim, row-major
  vectors: Int8Array
}

export function decodeEmbeddings(buf: ArrayBuffer): Embeddings {
  const headerLen = new DataView(buf).getUint32(0, true)
  const header = JSON.parse(new TextDecoder().decode(new Uint8Array(buf, 4, headerLen))) as {
    model: string
    dim: number
    ids: string[]
  }
  return {
    model: header.model,
    dim: header.dim,
    ids: header.ids,
    vectors: new Int8Array(buf, 4 + headerLen),
  }
}

// -- IndexedDB (thin, manual-QA only — vitest env is `node`, no indexedDB) --

interface Cached {
  key: string
  libraryFolderId: string
  modifiedTime: string | null
  buf: ArrayBuffer
}

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 1)
    req.onupgradeneeded = () => {
      if (!req.result.objectStoreNames.contains(STORE)) {
        req.result.createObjectStore(STORE, { keyPath: 'key' })
      }
    }
    req.onsuccess = () => resolve(req.result)
    req.onerror = () => reject(req.error ?? new Error('IndexedDB open failed'))
  })
}

async function readCache(): Promise<Cached | null> {
  if (typeof indexedDB === 'undefined') return null
  try {
    const db = await openDb()
    const rec = await new Promise<Cached | undefined>((resolve, reject) => {
      const r = db.transaction(STORE, 'readonly').objectStore(STORE).get(RECORD_KEY)
      r.onsuccess = () => resolve(r.result as Cached | undefined)
      r.onerror = () => reject(r.error)
    })
    db.close()
    return rec ?? null
  } catch {
    return null
  }
}

async function writeCache(rec: Cached): Promise<void> {
  if (typeof indexedDB === 'undefined') return
  try {
    const db = await openDb()
    await new Promise<void>((resolve, reject) => {
      const t = db.transaction(STORE, 'readwrite')
      t.objectStore(STORE).put(rec)
      t.oncomplete = () => resolve()
      t.onerror = () => reject(t.error)
    })
    db.close()
  } catch {
    /* full / unavailable — we just re-download next time */
  }
}

// null = there's no embeddings sidecar in this library yet.
export async function fetchEmbeddings(
  token: string,
  libraryFolderId: string,
): Promise<Embeddings | null> {
  const cached = await readCache()
  const cacheValid = cached?.libraryFolderId === libraryFolderId
  try {
    const query = encodeURIComponent(
      `'${libraryFolderId}' in parents and name = '${FILENAME}' and trashed = false`,
    )
    const listResp = await fetch(
      `https://www.googleapis.com/drive/v3/files?q=${query}&fields=files(id,modifiedTime)&pageSize=1`,
      { headers: { Authorization: `Bearer ${token}` } },
    )
    if (!listResp.ok) throw new Error(`list ${listResp.status}`)
    const { files } = (await listResp.json()) as { files: { id: string; modifiedTime: string }[] }
    if (files.length === 0) return cacheValid ? decodeEmbeddings(cached!.buf) : null

    const { id, modifiedTime } = files[0]
    if (cacheValid && cached!.modifiedTime === modifiedTime) return decodeEmbeddings(cached!.buf)

    const fileResp = await fetch(`https://www.googleapis.com/drive/v3/files/${id}?alt=media`, {
      headers: { Authorization: `Bearer ${token}` },
    })
    if (!fileResp.ok) throw new Error(`download ${fileResp.status}`)
    const buf = await fileResp.arrayBuffer()
    await writeCache({ key: RECORD_KEY, libraryFolderId, modifiedTime, buf })
    return decodeEmbeddings(buf)
  } catch {
    return cacheValid ? decodeEmbeddings(cached!.buf) : null
  }
}
