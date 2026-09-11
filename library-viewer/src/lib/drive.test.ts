import { afterEach, describe, expect, it, vi } from 'vitest'
import { isSupportedEbook, looksLikeZip, writeJsonFile } from './drive'
import { setTokenRefresher } from './tokenBroker'

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

describe('writeJsonFile 401 handling', () => {
  afterEach(() => {
    setTokenRefresher(null)
    vi.restoreAllMocks()
  })

  it('refreshes the token and retries once on a 401', async () => {
    const tokens: string[] = []
    setTokenRefresher(async () => 'fresh-token')
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_url: string, opts: { headers: Record<string, string> }) => {
        tokens.push(opts.headers.Authorization)
        return tokens.length === 1
          ? new Response('', { status: 401 })
          : new Response(JSON.stringify({ id: 'f1' }), { status: 200 })
      }),
    )
    const id = await writeJsonFile('stale-token', 'folder', 'x.json', { a: 1 }, 'f1')
    expect(id).toBe('f1')
    expect(tokens).toEqual(['Bearer stale-token', 'Bearer fresh-token'])
  })

  it('gives a sign-in error when the refresh also fails', async () => {
    setTokenRefresher(async () => {
      throw new Error('Sign-in expired — sign in again.')
    })
    vi.stubGlobal('fetch', vi.fn(async () => new Response('', { status: 401 })))
    await expect(writeJsonFile('t', 'folder', 'x.json', {}, 'f1')).rejects.toThrow(/sign-in expired/i)
  })
})
