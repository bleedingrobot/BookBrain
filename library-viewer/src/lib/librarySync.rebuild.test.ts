import { beforeEach, describe, expect, it, vi } from 'vitest'

// Mock the Drive layer; keep the real StalePageTokenError + constants.
vi.mock('./drive', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./drive')>()
  return {
    ...actual,
    listAllChanges: vi.fn(),
    listLibraryTree: vi.fn(),
    getStartPageToken: vi.fn(),
  }
})

import { getStartPageToken, listAllChanges, listLibraryTree, StalePageTokenError } from './drive'
import { syncLibrary, type LibraryCache } from './librarySync'

const LIB = 'lib-root'
const CACHE_KEY = 'bookbrain.libraryCache'

const freshCache: LibraryCache = {
  libraryFolderId: LIB,
  pageToken: 't0',
  files: [{ id: 'book-1', name: 'A.epub' }],
  folderIds: [LIB],
  builtAt: Date.now(),
}

beforeEach(() => {
  localStorage.clear()
  vi.clearAllMocks()
  vi.mocked(getStartPageToken).mockResolvedValue('t-new')
  vi.mocked(listLibraryTree).mockResolvedValue({ files: [{ id: 'b', name: 'b.epub' }], folderIds: [LIB] })
})

describe('syncLibrary error handling', () => {
  it('rebuilds on a StalePageTokenError', async () => {
    localStorage.setItem(CACHE_KEY, JSON.stringify(freshCache))
    vi.mocked(listAllChanges).mockRejectedValue(new StalePageTokenError('expired'))

    const { rebuilt } = await syncLibrary('tok', LIB)

    expect(rebuilt).toBe(true)
    expect(listLibraryTree).toHaveBeenCalledOnce()
  })

  it('does NOT rebuild — it rethrows — on a transient error', async () => {
    localStorage.setItem(CACHE_KEY, JSON.stringify(freshCache))
    vi.mocked(listAllChanges).mockRejectedValue(new Error('Drive API error (500)'))

    await expect(syncLibrary('tok', LIB)).rejects.toThrow(/500/)
    expect(listLibraryTree).not.toHaveBeenCalled()
  })

  it('still rebuilds when there is no cache', async () => {
    vi.mocked(listAllChanges).mockResolvedValue({ changes: [], newStartPageToken: 't1' })

    const { rebuilt } = await syncLibrary('tok', LIB)

    expect(rebuilt).toBe(true)
    expect(listAllChanges).not.toHaveBeenCalled()
  })

  it('applies changes normally on success', async () => {
    localStorage.setItem(CACHE_KEY, JSON.stringify(freshCache))
    vi.mocked(listAllChanges).mockResolvedValue({ changes: [], newStartPageToken: 't1' })

    const { rebuilt, cache } = await syncLibrary('tok', LIB)

    expect(rebuilt).toBe(false)
    expect(cache.pageToken).toBe('t1')
  })
})
