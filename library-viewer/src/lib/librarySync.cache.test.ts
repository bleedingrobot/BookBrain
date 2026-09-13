import { beforeEach, describe, expect, it, vi } from 'vitest'
import { loadCachedFiles, saveCache, type LibraryCache } from './librarySync'

const LIB = 'lib-root'
const LEGACY_KEY = 'bookbrain.libraryCache'

const cache: LibraryCache = {
  libraryFolderId: LIB,
  pageToken: 't0',
  files: [{ id: 'book-1', name: 'A.epub' }],
  folderIds: [LIB],
  builtAt: Date.now(),
}

beforeEach(() => {
  localStorage.clear()
})

describe('library cache (IndexedDB)', () => {
  it('returns null when nothing has been cached', async () => {
    expect(await loadCachedFiles(LIB)).toBeNull()
  })

  it('round-trips a saved cache for the matching library folder', async () => {
    await saveCache(cache)
    expect(await loadCachedFiles(LIB)).toEqual(cache.files)
  })

  it('returns null for a different library folder than the one cached', async () => {
    await saveCache(cache)
    expect(await loadCachedFiles('some-other-folder')).toBeNull()
  })

  it('carries over a pre-migration cache still sitting in localStorage', async () => {
    localStorage.setItem(LEGACY_KEY, JSON.stringify(cache))
    expect(await loadCachedFiles(LIB)).toEqual(cache.files)
    // and cleans up after itself so it isn't re-read every load
    expect(localStorage.getItem(LEGACY_KEY)).toBeNull()
  })

  it('never throws even if the underlying store rejects the write', async () => {
    const openSpy = vi.spyOn(indexedDB, 'open').mockImplementation(() => {
      throw new DOMException('exceeded the quota', 'QuotaExceededError')
    })
    await expect(saveCache(cache)).resolves.toBeUndefined()
    openSpy.mockRestore()
  })

  it('degrades to a no-op when IndexedDB is unavailable entirely', async () => {
    const original = globalThis.indexedDB
    // @ts-expect-error — simulating an old browser / locked-down context
    delete globalThis.indexedDB
    await expect(saveCache(cache)).resolves.toBeUndefined()
    expect(await loadCachedFiles(LIB)).toBeNull()
    globalThis.indexedDB = original
  })
})
