import { describe, expect, it } from 'vitest'
import { clientRectsToBoxes, isMeaningfulSelection, mergeLineRects, normaliseSelectedText } from './selection'
import { testViewport } from './testViewport'

describe('normaliseSelectedText', () => {
  it('joins lines with spaces and keeps a line-end hyphen without the break', () => {
    expect(normaliseSelectedText('We call our particular atten-\ntion “Scaled Dot-Product\nAttention”  ')).toBe(
      'We call our particular atten-tion “Scaled Dot-Product Attention”',
    )
  })
})

describe('isMeaningfulSelection', () => {
  it('needs two non-space characters', () => {
    expect(isMeaningfulSelection(' a ')).toBe(false)
    expect(isMeaningfulSelection('a b')).toBe(true)
    expect(isMeaningfulSelection('dk')).toBe(true)
  })
})

describe('mergeLineRects', () => {
  it('merges the runs of one line into a single band and keeps lines apart', () => {
    const merged = mergeLineRects([
      { left: 10, top: 100, width: 50, height: 12 },
      { left: 62, top: 101, width: 40, height: 11 },
      { left: 104, top: 100, width: 30, height: 12 },
      { left: 10, top: 114, width: 120, height: 12 },
    ])
    expect(merged).toHaveLength(2)
    expect(merged[0]).toEqual({ left: 10, top: 100, width: 124, height: 12 })
    expect(merged[1]).toEqual({ left: 10, top: 114, width: 120, height: 12 })
  })

  it('keeps the two columns of a line apart', () => {
    const merged = mergeLineRects([
      { left: 10, top: 100, width: 200, height: 12 },
      { left: 260, top: 100, width: 200, height: 12 },
    ])
    expect(merged).toHaveLength(2)
  })

  it('drops empty rects', () => {
    expect(mergeLineRects([{ left: 10, top: 10, width: 0, height: 12 }])).toEqual([])
  })
})

describe('clientRectsToBoxes', () => {
  const vp = testViewport({ viewBox: [0, 0, 612, 792], scale: 1.5 })
  // Two pages stacked in the scroller: page 3 at y 100, page 4 at y 100 + 792·1.5 + 16.
  const p3 = { index: 3, rect: { left: 50, top: 100, right: 50 + 918, bottom: 100 + 1188 }, vp }
  const p4top = 100 + 1188 + 16
  const p4 = { index: 4, rect: { left: 50, top: p4top, right: 50 + 918, bottom: p4top + 1188 }, vp }

  it('maps client rects on one page back to server boxes, one per line', () => {
    const { boxes } = clientRectsToBoxes(
      [
        { left: 50 + 150, top: 100 + 300, width: 300, height: 15 },
        { left: 50 + 455, top: 100 + 300, width: 100, height: 15 },
        { left: 50 + 150, top: 100 + 318, width: 380, height: 15 },
      ],
      [p3],
    )
    expect(boxes).toEqual([
      { page: 3, x0: 100, y0: 200, x1: 370, y1: 210 },
      { page: 3, x0: 100, y0: 212, x1: 353.3, y1: 222 },
    ])
  })

  it('splits a selection that crosses a page break by page, in reading order', () => {
    const { boxes } = clientRectsToBoxes(
      [
        { left: 50 + 150, top: p4top + 120, width: 300, height: 15 },
        { left: 50 + 150, top: 100 + 1050, width: 300, height: 15 },
      ],
      [p4, p3],
    )
    expect(boxes.map((b) => b.page)).toEqual([3, 4])
    expect(boxes[0]!.y0).toBeCloseTo(700)
    expect(boxes[1]!.y0).toBeCloseTo(80)
  })

  it('ignores rects outside every page (the gap between pages)', () => {
    const { boxes } = clientRectsToBoxes([{ left: 200, top: 100 + 1188 + 4, width: 300, height: 8 }], [p3, p4])
    expect(boxes).toEqual([])
  })
})
