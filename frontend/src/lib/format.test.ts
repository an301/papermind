import { describe, expect, it } from 'vitest'
import { authorsCitation, authorsShort, listJoin, megabytes, NBSP, pageLabel, pageRangeLabel, pagesLabel, plural, smarten } from './format'

describe('format (house typography, DESIGN.md §7.1)', () => {
  it('shortens author lists the way the Library row does', () => {
    expect(authorsShort(['Ashish Vaswani'], 2017)).toBe('Vaswani, 2017')
    expect(authorsShort(['Ashish Vaswani', 'Noam Shazeer'], 2017)).toBe('Vaswani and Shazeer, 2017')
    expect(authorsShort(['Ashish Vaswani', 'Noam Shazeer', 'Niki Parmar'], 2017)).toBe(`Vaswani et${NBSP}al., 2017`)
    expect(authorsShort([], 2017)).toBe('2017')
    expect(authorsCitation(['Ashish Vaswani', 'Noam Shazeer', 'Niki Parmar'], 2017)).toBe(`Vaswani et${NBSP}al. (2017)`)
  })

  it('glues numbers to their units and uses an en dash in ranges', () => {
    expect(pageLabel(3)).toBe(`p.${NBSP}4`)
    expect(pageRangeLabel(3, 4)).toBe(`pp.${NBSP}4–5`)
    expect(pageRangeLabel(3, 3)).toBe(`p.${NBSP}4`)
    expect(pagesLabel(1)).toBe(`1${NBSP}page`)
    expect(megabytes(72 * 1024 * 1024)).toBe(`72${NBSP}MB`)
    expect(megabytes(1.25 * 1024 * 1024)).toBe(`1.3${NBSP}MB`)
  })

  it('joins and counts in plain English', () => {
    expect(listJoin(['softmax'])).toBe('softmax')
    expect(listJoin(['softmax', 'the dot product', 'variance'])).toBe('softmax, the dot product and variance')
    expect(plural(3712, 'concept')).toBe('3,712 concepts')
    expect(plural(1, 'concept')).toBe('1 concept')
  })

  it('smartens straight quotes and three dots from server strings', () => {
    expect(smarten(`It's "scaled"... isn't it`)).toBe('It’s “scaled”… isn’t it')
  })
})
