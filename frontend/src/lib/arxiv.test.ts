import { describe, expect, it } from 'vitest'
import { parseArxiv } from './arxiv'

describe('parseArxiv (DESIGN.md §5.4)', () => {
  it.each([
    ['1706.03762', '1706.03762', null],
    ['1706.03762v5', '1706.03762', 'v5'],
    ['arXiv:1706.03762', '1706.03762', null],
    ['  arxiv.org/abs/1706.03762  ', '1706.03762', null],
    ['https://arxiv.org/pdf/1706.03762v5', '1706.03762', 'v5'],
    ['https://www.arxiv.org/pdf/1706.03762.pdf', '1706.03762', null],
    ['http://arxiv.org/abs/2401.12345v2?context=cs', '2401.12345', 'v2'],
    ['cs/0112017', 'cs/0112017', null],
    ['math.GT/0309136', 'math/0309136', null],
    ['https://arxiv.org/abs/hep-th/9901001v3', 'hep-th/9901001', 'v3'],
  ])('accepts %s', (raw, id, version) => {
    const ref = parseArxiv(raw)
    expect(ref?.arxivId).toBe(id)
    expect(ref?.version).toBe(version)
  })

  it.each(['', 'attention is all you need', '1706.037', '1713.03762', 'https://example.com/1706.03762', '2401.12345v0'])(
    'rejects %j',
    (raw) => {
      expect(parseArxiv(raw)).toBeNull()
    },
  )

  it('maps old-style ids to the library paper id', () => {
    expect(parseArxiv('cs/0112017')?.paperId).toBe('cs_0112017')
    expect(parseArxiv('1706.03762v7')?.display).toBe('1706.03762v7')
  })
})

describe('parseArxiv: what people copy from the abs page', () => {
  it('accepts the "Cite as" line and the DOI', () => {
    expect(parseArxiv('arXiv:1706.03762 [cs.CL]')?.display).toBe('1706.03762')
    expect(parseArxiv('10.48550/arXiv.1706.03762')?.display).toBe('1706.03762')
    expect(parseArxiv('https://doi.org/10.48550/arXiv.1706.03762v7')?.display).toBe('1706.03762v7')
    expect(parseArxiv('see 1706.03762 here')).toBeNull()
  })
})
