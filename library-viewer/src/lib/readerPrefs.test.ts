import { describe, expect, it } from 'vitest'
import { DEFAULT_PREFS, loadReaderPrefs, readerCss, saveReaderPrefs } from './readerPrefs'

describe('readerPrefs', () => {
  it('returns defaults when nothing is stored', () => {
    expect(loadReaderPrefs()).toEqual(DEFAULT_PREFS)
  })

  it('round-trips valid prefs', () => {
    const prefs = { ...DEFAULT_PREFS, fontSize: 130, theme: 'dark' as const, font: 'serif' as const }
    saveReaderPrefs(prefs)
    expect(loadReaderPrefs()).toEqual(prefs)
  })

  it('clamps out-of-range values and rejects unknown enums', () => {
    localStorage.setItem(
      'bookbrain.readerPrefs',
      JSON.stringify({ fontSize: 9000, lineHeight: 0.1, margin: -5, theme: 'neon', font: 'comic' }),
    )
    const p = loadReaderPrefs()
    expect(p.fontSize).toBe(180)
    expect(p.lineHeight).toBe(1.2)
    expect(p.margin).toBe(20)
    expect(p.theme).toBe('light')
    expect(p.font).toBe('publisher')
  })

  it('readerCss reflects theme + font size', () => {
    const css = readerCss({ ...DEFAULT_PREFS, fontSize: 120, theme: 'sepia' })
    expect(css).toContain('font-size: 120%')
    expect(css).toContain('#f4ecd8') // sepia bg
  })
})
