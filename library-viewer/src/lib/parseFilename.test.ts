import { describe, expect, it } from 'vitest'
import { parseFilename } from './parseFilename'

describe('parseFilename', () => {
  it('single field → title only', () => {
    expect(parseFilename('Dune.epub')).toEqual({
      author: null,
      title: 'Dune',
      series: null,
      seriesNumber: null,
    })
  })

  it('two fields → author, title', () => {
    expect(parseFilename('Frank Herbert, Dune.epub')).toMatchObject({
      author: 'Frank Herbert',
      title: 'Dune',
      series: null,
    })
  })

  it('three fields → author, title, series', () => {
    expect(parseFilename('Frank Herbert, Dune, Dune Chronicles.epub')).toMatchObject({
      author: 'Frank Herbert',
      title: 'Dune',
      series: 'Dune Chronicles',
      seriesNumber: null,
    })
  })

  it('four+ fields → the rest is the series number', () => {
    expect(parseFilename('Frank Herbert, Dune Messiah, Dune Chronicles, 2.epub')).toMatchObject({
      author: 'Frank Herbert',
      title: 'Dune Messiah',
      series: 'Dune Chronicles',
      seriesNumber: '2',
    })
  })

  it('strips .kpub too, case-insensitively', () => {
    expect(parseFilename('Book.KPUB').title).toBe('Book')
  })

  it('strips .cbz comic archives too', () => {
    expect(parseFilename('Brian K. Vaughan, Saga, Saga, 1.cbz')).toEqual({
      author: 'Brian K. Vaughan',
      title: 'Saga',
      series: 'Saga',
      seriesNumber: '1',
    })
  })
})
