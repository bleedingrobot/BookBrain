import { describe, expect, it } from 'vitest'
import { applyChanges, type LibraryCache } from './librarySync'
import { FOLDER_MIME_TYPE, type DriveChange } from './drive'

const LIB = 'lib-root'

function cache(overrides: Partial<LibraryCache> = {}): LibraryCache {
  return {
    libraryFolderId: LIB,
    pageToken: 't0',
    files: [{ id: 'book-1', name: 'A Book.epub' }],
    folderIds: [LIB, 'series-folder'],
    builtAt: Date.now(),
    ...overrides,
  }
}

function fileChange(file: Partial<NonNullable<DriveChange['file']>> & { id: string }): DriveChange {
  return {
    fileId: file.id,
    removed: false,
    file: { name: 'A Book.epub', mimeType: 'application/epub+zip', ...file },
  }
}

describe('applyChanges — missing parents guard', () => {
  it('keeps a cached file when the change record omits parents', () => {
    const result = applyChanges(cache(), [fileChange({ id: 'book-1' })]) // no `parents` key
    expect(result.files.map((f) => f.id)).toContain('book-1')
  })

  it('still evicts a file moved to a folder outside the library', () => {
    const result = applyChanges(cache(), [
      fileChange({ id: 'book-1', parents: ['some-other-folder'] }),
    ])
    expect(result.files.map((f) => f.id)).not.toContain('book-1')
  })

  it('evicts on parents: [] (explicitly parentless)', () => {
    const result = applyChanges(cache(), [fileChange({ id: 'book-1', parents: [] })])
    expect(result.files.map((f) => f.id)).not.toContain('book-1')
  })

  it('evicts on removed: true', () => {
    const result = applyChanges(cache(), [{ fileId: 'book-1', removed: true }])
    expect(result.files.map((f) => f.id)).not.toContain('book-1')
  })

  it('evicts on trashed: true', () => {
    const result = applyChanges(cache(), [fileChange({ id: 'book-1', parents: ['series-folder'], trashed: true })])
    expect(result.files.map((f) => f.id)).not.toContain('book-1')
  })

  it('adds a new file whose parent is a known folder', () => {
    const result = applyChanges(cache(), [
      fileChange({ id: 'book-2', name: 'Another.epub', parents: ['series-folder'] }),
    ])
    expect(result.files.map((f) => f.id).sort()).toEqual(['book-1', 'book-2'])
  })

  it('does not add a never-seen file when the change omits parents', () => {
    const result = applyChanges(cache(), [fileChange({ id: 'book-2', name: 'Another.epub' })])
    expect(result.files.map((f) => f.id)).toEqual(['book-1'])
  })

  it('does not evict a known folder when its change omits parents', () => {
    const change: DriveChange = {
      fileId: 'series-folder',
      removed: false,
      file: { id: 'series-folder', name: 'Some Series', mimeType: FOLDER_MIME_TYPE },
    }
    const result = applyChanges(cache(), [change])
    expect(result.folderIds).toContain('series-folder')
  })

  it('still evicts a folder that moved out of the tree (populated parents, none known)', () => {
    const change: DriveChange = {
      fileId: 'series-folder',
      removed: false,
      file: {
        id: 'series-folder',
        name: 'Some Series',
        mimeType: FOLDER_MIME_TYPE,
        parents: ['elsewhere'],
      },
    }
    const result = applyChanges(cache(), [change])
    expect(result.folderIds).not.toContain('series-folder')
  })

  it('still resolves a nested brand-new folder regardless of change order', () => {
    const authorFolder: DriveChange = {
      fileId: 'author-f',
      removed: false,
      file: { id: 'author-f', name: 'Author', mimeType: FOLDER_MIME_TYPE, parents: [LIB] },
    }
    const seriesFolder: DriveChange = {
      fileId: 'series-f',
      removed: false,
      file: { id: 'series-f', name: 'Series', mimeType: FOLDER_MIME_TYPE, parents: ['author-f'] },
    }
    const book: DriveChange = fileChange({ id: 'b-new', name: 'New.epub', parents: ['series-f'] })
    // series folder listed before its parent author folder
    const result = applyChanges(cache(), [seriesFolder, authorFolder, book])
    expect(result.folderIds).toEqual(expect.arrayContaining(['author-f', 'series-f']))
    expect(result.files.map((f) => f.id)).toContain('b-new')
  })
})
