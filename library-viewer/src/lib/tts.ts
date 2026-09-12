// Read-aloud, built on the foliate-js TTS helper already vendored at
// vendor/foliate/tts.js (wired up via view.initTTS()) but never used anywhere
// in the app. That helper walks the current section's DOM one block
// (paragraph/heading/etc.) at a time and hands back each block as an SSML
// string with <mark> anchors inside it — it doesn't speak anything itself.
//
// The actual voice is the browser's built-in Web Speech API
// (speechSynthesis), which can't consume SSML, so each block's SSML is
// flattened to plain text before it's spoken.
//
// Keeping the page in sync with what's being read reuses foliate's own
// tts.setMark('0') — the first mark of a freshly-fetched block — which
// triggers its default highlight callback (scrollToAnchor), turning the page
// if the block isn't currently visible. That's block-by-block, not
// word-by-word: a live word-level highlight would need mapping the Web
// Speech API's boundary-event character offsets back to foliate's internal
// word segmentation, which isn't reliable enough across browsers to be worth
// the complexity here — see prompts/ if picking this up later.
import type { FoliateView } from '../vendor/foliate/view.js'

export type TtsStatus = 'idle' | 'playing' | 'paused' | 'finished' | 'error'

export interface TtsControllerOptions {
  view: FoliateView
  onStatusChange: (status: TtsStatus, message?: string) => void
  getRate: () => number
  getVoiceURI: () => string | null
}

export function isTtsSupported(): boolean {
  return typeof window !== 'undefined' && 'speechSynthesis' in window
}

export function getVoices(): SpeechSynthesisVoice[] {
  if (!isTtsSupported()) return []
  return window.speechSynthesis.getVoices()
}

// Chrome (and some others) populate the voice list asynchronously — the
// first getVoices() call right after page load often returns [].
export function onVoicesChanged(cb: () => void): () => void {
  if (!isTtsSupported()) return () => {}
  const synth = window.speechSynthesis
  synth.addEventListener('voiceschanged', cb)
  return () => synth.removeEventListener('voiceschanged', cb)
}

// Turns one block's SSML (an XML string with <speak>/<mark>/<emphasis>/...)
// into plain text. A parse failure (malformed markup foliate didn't expect)
// falls back to a blunt tag-strip rather than losing the block entirely.
// DOMParser is browser-only, so the fallback path is also what actually runs
// under this project's node-environment tests (see tts.test.ts) — the
// primary path only gets exercised by hand in an actual browser.
export function ssmlToText(ssml: string): string {
  try {
    const doc = new DOMParser().parseFromString(ssml, 'application/xml')
    if (doc.querySelector('parsererror')) throw new Error('parse error')
    return (doc.documentElement.textContent ?? '').replace(/\s+/g, ' ').trim()
  } catch {
    return ssml
      .replace(/<[^>]+>/g, ' ')
      .replace(/\s+/g, ' ')
      .trim()
  }
}

const MAX_EMPTY_BLOCK_SKIPS = 50
const SECTION_ADVANCE_TIMEOUT_MS = 500
const SECTION_ADVANCE_POLL_MS = 40

export class TtsController {
  #view: FoliateView
  #onStatusChange: TtsControllerOptions['onStatusChange']
  #getRate: () => number
  #getVoiceURI: () => string | null
  #status: TtsStatus = 'idle'
  // Set right before a deliberate cancel() (skip/stop) so the utterance's
  // own end/error handler knows not to also auto-advance.
  #suppressAutoAdvance = false

  constructor(opts: TtsControllerOptions) {
    this.#view = opts.view
    this.#onStatusChange = opts.onStatusChange
    this.#getRate = opts.getRate
    this.#getVoiceURI = opts.getVoiceURI
  }

  get status(): TtsStatus {
    return this.#status
  }

  #setStatus(status: TtsStatus, message?: string) {
    this.#status = status
    this.#onStatusChange(status, message)
  }

  // Fresh start from wherever the reader currently is (not necessarily the
  // top of the section) — falls back to the section start if foliate hasn't
  // reported a position range yet.
  async play(): Promise<void> {
    if (!isTtsSupported()) {
      this.#setStatus('error', "This browser doesn't support read-aloud.")
      return
    }
    if (this.#status === 'paused' && window.speechSynthesis.paused) {
      window.speechSynthesis.resume()
      this.#setStatus('playing')
      return
    }
    window.speechSynthesis.cancel()
    try {
      await this.#view.initTTS('sentence')
    } catch {
      this.#setStatus('error', 'Could not prepare this page for read-aloud.')
      return
    }
    const tts = this.#view.tts
    if (!tts) {
      this.#setStatus('error', 'Could not prepare this page for read-aloud.')
      return
    }
    const range = this.#view.lastLocation?.range
    const ssml = range ? tts.from(range) : tts.start()
    await this.#speakFrom(ssml)
  }

  pause(): void {
    if (!isTtsSupported()) return
    window.speechSynthesis.pause()
    this.#setStatus('paused')
  }

  stop(): void {
    if (!isTtsSupported()) return
    this.#suppressAutoAdvance = true
    window.speechSynthesis.cancel()
    this.#setStatus('idle')
  }

  async skipForward(): Promise<void> {
    await this.#skip('next')
  }

  async skipBack(): Promise<void> {
    await this.#skip('prev')
  }

  destroy(): void {
    this.stop()
  }

  async #skip(direction: 'next' | 'prev'): Promise<void> {
    if (!isTtsSupported()) return
    const tts = this.#view.tts
    if (!tts) return
    this.#suppressAutoAdvance = true
    window.speechSynthesis.cancel()
    const ssml = direction === 'next' ? tts.next() : tts.prev()
    if (ssml === undefined) {
      // Ran off the end/start of this section's blocks.
      if (direction === 'next') {
        const moved = await this.#advanceToNextSection()
        if (!moved) {
          this.#setStatus('finished')
          return
        }
        await this.play()
      } else {
        this.#setStatus('paused')
      }
      return
    }
    await this.#speakFrom(ssml)
  }

  // Feeds one block's SSML to the browser voice, then (once it finishes)
  // recurses onto the next block — this is the main read-through loop.
  async #speakFrom(ssml: string | undefined, emptySkips = 0): Promise<void> {
    if (ssml === undefined) {
      const moved = await this.#advanceToNextSection()
      if (!moved) {
        this.#setStatus('finished')
        return
      }
      return this.play()
    }

    const tts = this.#view.tts
    tts?.setMark('0') // best-effort: turns the page to this block if needed

    const text = ssmlToText(ssml)
    if (!text) {
      // A block with nothing to say (an image, a blank <hr> divider, ...) —
      // move straight on rather than stalling on silence.
      if (emptySkips >= MAX_EMPTY_BLOCK_SKIPS) {
        this.#setStatus('finished')
        return
      }
      return this.#speakFrom(tts?.next(), emptySkips + 1)
    }

    const utterance = new SpeechSynthesisUtterance(text)
    utterance.rate = this.#getRate()
    const voiceURI = this.#getVoiceURI()
    if (voiceURI) {
      const voice = getVoices().find((v) => v.voiceURI === voiceURI)
      if (voice) utterance.voice = voice
    }
    utterance.onend = () => {
      if (this.#suppressAutoAdvance) {
        this.#suppressAutoAdvance = false
        return
      }
      void this.#speakFrom(tts?.next())
    }
    utterance.onerror = (e) => {
      if (this.#suppressAutoAdvance || e.error === 'canceled' || e.error === 'interrupted') {
        this.#suppressAutoAdvance = false
        return
      }
      this.#setStatus('error', 'Read-aloud hit a problem and stopped.')
    }
    this.#setStatus('playing')
    window.speechSynthesis.speak(utterance)
  }

  // The block iterator only covers the section that was loaded when
  // initTTS() last ran. Once it's exhausted, page forward with the normal
  // paginator (which crosses into the next chapter file on its own) and
  // detect real movement by watching lastLocation's fraction, since the
  // relocate event that updates it can land a beat after next() resolves.
  async #advanceToNextSection(): Promise<boolean> {
    const before = this.#view.lastLocation?.fraction ?? 0
    await this.#view.renderer.next()
    const deadline = Date.now() + SECTION_ADVANCE_TIMEOUT_MS
    while (Date.now() < deadline) {
      const after = this.#view.lastLocation?.fraction ?? 0
      if (after > before) return true
      await new Promise((r) => setTimeout(r, SECTION_ADVANCE_POLL_MS))
    }
    return (this.#view.lastLocation?.fraction ?? 0) > before
  }
}
