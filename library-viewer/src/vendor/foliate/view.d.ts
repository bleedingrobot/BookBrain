// Hand-written types for the vendored foliate-js EPUB path (prompts/17).
// Covers only what the library-viewer Reader touches — see ./VERSION.

export interface FoliateTOCItem {
  label: string
  href: string
  subitems?: FoliateTOCItem[] | null
}

export interface FoliateBook {
  metadata?: {
    title?: string | Record<string, string>
    author?: unknown
    language?: string | string[]
  }
  toc?: FoliateTOCItem[] | null
  dir?: string
  getCover?: () => Promise<Blob | null> | Blob | null
}

export interface FoliateRelocateDetail {
  fraction: number
  location?: { current: number; next?: number; total: number }
  cfi?: string
  tocItem?: { label?: string; href?: string } | null
  pageItem?: { label?: string } | null
  range?: Range
}

export interface FoliateRenderer extends HTMLElement {
  setStyles?: (css: string) => void
  render?: () => void
  next: () => Promise<void>
  prev: () => Promise<void>
  getContents: () => { doc: Document; index: number }[]
}

// prompts/ (TTS) — foliate's own read-aloud block walker. Speaks nothing by
// itself: each call hands back one block of the book as an SSML string with
// <mark> anchors inside it, which lib/tts.ts flattens to plain text and feeds
// to the browser's Web Speech API. setMark() re-triggers the block's default
// highlight (a page-turn via scrollToAnchor if it's off-screen).
export interface FoliateTTS {
  doc: Document
  start: () => string | undefined
  resume: () => string | undefined
  prev: (paused?: boolean) => string | undefined
  next: (paused?: boolean) => string | undefined
  from: (range: Range) => string | undefined
  setMark: (mark: string) => void
}

export interface FoliateView extends HTMLElement {
  open: (book: File | Blob | string) => Promise<void>
  init: (opts: { lastLocation?: string; showTextStart?: boolean }) => Promise<void>
  close: () => void
  book: FoliateBook
  renderer: FoliateRenderer
  lastLocation?: FoliateRelocateDetail | null
  tts?: FoliateTTS | null
  initTTS: (granularity?: 'word' | 'sentence', highlight?: (range: Range) => void) => Promise<void>
  goTo: (target: string | number) => Promise<unknown>
  goToFraction: (fraction: number) => Promise<void>
  goLeft: () => Promise<void>
  goRight: () => Promise<void>
  next: (distance?: number) => Promise<void>
  prev: (distance?: number) => Promise<void>
  getSectionFractions: () => number[]
  addEventListener(
    type: 'relocate',
    listener: (e: CustomEvent<FoliateRelocateDetail>) => void,
  ): void
  addEventListener(
    type: 'load',
    listener: (e: CustomEvent<{ doc: Document; index: number }>) => void,
  ): void
  addEventListener(type: string, listener: EventListenerOrEventListenerObject): void
  removeEventListener(type: string, listener: EventListenerOrEventListenerObject): void
}

export function makeBook(file: File | string): Promise<FoliateBook>
