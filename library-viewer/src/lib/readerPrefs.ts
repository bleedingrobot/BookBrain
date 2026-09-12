// Reader typography + theme. Per-device (localStorage) by design — these are
// about this screen, not something to sync.

const KEY = 'bookbrain.readerPrefs'

export type ReaderTheme = 'light' | 'sepia' | 'dark'
export type ReaderFont = 'publisher' | 'serif' | 'sans'

export interface ReaderPrefs {
  fontSize: number // percent, 80..180
  font: ReaderFont
  lineHeight: number // 1.2..2.0
  margin: number // px, 20..100
  theme: ReaderTheme
}

export const DEFAULT_PREFS: ReaderPrefs = {
  fontSize: 100,
  font: 'publisher',
  lineHeight: 1.5,
  margin: 48,
  theme: 'light',
}

const clamp = (n: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, n))

export function loadReaderPrefs(): ReaderPrefs {
  try {
    const raw = localStorage.getItem(KEY)
    if (!raw) return { ...DEFAULT_PREFS }
    const p = JSON.parse(raw) as Partial<ReaderPrefs>
    return {
      fontSize: clamp(Number(p.fontSize) || DEFAULT_PREFS.fontSize, 80, 180),
      font: p.font === 'serif' || p.font === 'sans' ? p.font : 'publisher',
      lineHeight: clamp(Number(p.lineHeight) || DEFAULT_PREFS.lineHeight, 1.2, 2),
      margin: clamp(Number(p.margin) || DEFAULT_PREFS.margin, 20, 100),
      theme: p.theme === 'sepia' || p.theme === 'dark' ? p.theme : 'light',
    }
  } catch {
    return { ...DEFAULT_PREFS }
  }
}

export function saveReaderPrefs(prefs: ReaderPrefs): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(prefs))
  } catch {
    // ignore
  }
}

const THEME_COLORS: Record<ReaderTheme, { bg: string; fg: string; link: string }> = {
  light: { bg: '#ffffff', fg: '#1a1a1a', link: '#1155cc' },
  sepia: { bg: '#f4ecd8', fg: '#5b4636', link: '#7a5a2e' },
  dark: { bg: '#121212', fg: '#cfcfcf', link: '#8ab4f8' },
}

export function themeColors(theme: ReaderTheme) {
  return THEME_COLORS[theme]
}

const FONT_STACK: Record<ReaderFont, string | null> = {
  publisher: null,
  serif: 'Georgia, "Iowan Old Style", "Palatino Linotype", serif',
  sans: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
}

// CSS injected into every section document via renderer.setStyles().
export function readerCss(prefs: ReaderPrefs): string {
  const { bg, fg, link } = THEME_COLORS[prefs.theme]
  const family = FONT_STACK[prefs.font]
  return `
    @namespace epub "http://www.idpf.org/2007/ops";
    html {
      color-scheme: ${prefs.theme === 'dark' ? 'dark' : 'light'};
      color: ${fg} !important;
      background: ${bg} !important;
      font-size: ${prefs.fontSize}% !important;
    }
    body {
      color: ${fg} !important;
      background: ${bg} !important;
      ${family ? `font-family: ${family} !important;` : ''}
    }
    p, li, blockquote, dd, div {
      line-height: ${prefs.lineHeight} !important;
      ${family ? `font-family: ${family} !important;` : ''}
    }
    a, a:link, a:visited { color: ${link} !important; }
    img { max-width: 100% !important; height: auto !important; }
    aside[epub|type~="endnote"], aside[epub|type~="footnote"],
    aside[epub|type~="note"], aside[epub|type~="rearnote"] { display: none; }
  `
}
