import { describe, expect, it } from 'vitest'
import { isSupportedEbook, looksLikeZip } from './drive'

describe('looksLikeZip', () => {
  it('accepts a PK-prefixed blob (EPUB/ZIP magic)', async () => {
    const blob = new Blob([new Uint8Array([0x50, 0x4b, 0x03, 0x04, 0, 0])])
    expect(await looksLikeZip(blob)).toBe(true)
  })

  it('rejects an HTML/JSON error page', async () => {
    expect(await looksLikeZip(new Blob(['<!DOCTYPE html><html>...']))).toBe(false)
    expect(await looksLikeZip(new Blob(['{"error":{"code":403}}']))).toBe(false)
  })

  it('rejects a truncated blob', async () => {
    expect(await looksLikeZip(new Blob([new Uint8Array([0x50])]))).toBe(false)
  })
})

describe('isSupportedEbook', () => {
  it('matches the ebook extensions case-insensitively', () => {
    expect(isSupportedEbook('Author, Title.epub')).toBe(true)
    expect(isSupportedEbook('X.EPUB')).toBe(true)
    expect(isSupportedEbook('X.cbz')).toBe(true)
    expect(isSupportedEbook('notes.txt')).toBe(false)
  })
})
