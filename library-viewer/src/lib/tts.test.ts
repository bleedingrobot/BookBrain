import { describe, expect, it } from 'vitest'
import { ssmlToText } from './tts'

// DOMParser doesn't exist under this project's node test environment, so
// ssmlToText always takes its regex fallback path here — the same path a
// real browser would only hit if XML parsing failed. The primary
// DOMParser-based path is exercised by hand in an actual browser, not here.
describe('ssmlToText', () => {
  it('strips tags and collapses whitespace', () => {
    expect(ssmlToText('<speak>Hello <emphasis>world</emphasis>.</speak>')).toBe('Hello world .')
  })

  it('drops marks and breaks, keeping surrounding words in order', () => {
    const ssml =
      '<speak><mark name="0"/>The quick<mark name="1"/> <break time="200ms"/>brown fox.</speak>'
    expect(ssmlToText(ssml)).toBe('The quick brown fox.')
  })

  it('returns an empty string for a block with nothing to say', () => {
    expect(ssmlToText('<speak>   </speak>')).toBe('')
  })
})
