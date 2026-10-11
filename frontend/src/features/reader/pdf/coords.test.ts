import { describe, expect, it } from 'vitest'
import { boxToCss, boxToPage, boxToPercent, cssToBox } from './coords'
import { testViewport } from './testViewport'

// The title box on page 1 of Attention Is All You Need, from data/papers/1706.03762/paper.json.
const TITLE = { page: 0, x0: 211.5, y0: 148.8, x1: 399.9, y1: 166.0 }

describe('boxToCss', () => {
  it('is a plain multiply on an unrotated page whose CropBox starts at the origin (every arXiv PDF)', () => {
    const vp = testViewport({ viewBox: [0, 0, 612, 792], scale: 1.5 })
    const r = boxToCss(TITLE, vp)
    expect(r.left).toBeCloseTo(211.5 * 1.5)
    expect(r.top).toBeCloseTo(148.8 * 1.5)
    expect(r.width).toBeCloseTo((399.9 - 211.5) * 1.5)
    expect(r.height).toBeCloseTo((166.0 - 148.8) * 1.5)
  })

  it('follows a CropBox that does not start at the origin (PyMuPDF boxes are relative to the CropBox)', () => {
    const vp = testViewport({ viewBox: [36, 50, 576, 742], scale: 1 })
    const r = boxToCss({ x0: 0, y0: 0, x1: 100, y1: 20 }, vp)
    expect(r).toEqual({ left: 0, top: 0, width: 100, height: 20 })
  })

  it('rotates with the page: on a /Rotate 90 page the top-left of the unrotated page lands at the top-right', () => {
    // Unrotated 612 × 792 portrait page, displayed landscape (792 wide, 612 tall).
    const vp = testViewport({ viewBox: [0, 0, 612, 792], scale: 1, rotation: 90 })
    expect(vp.width).toBe(792)
    expect(vp.height).toBe(612)
    const r = boxToCss({ x0: 0, y0: 0, x1: 100, y1: 20 }, vp)
    // The unrotated top edge becomes the right edge; x along it runs down the screen.
    expect(r.left).toBeCloseTo(792 - 20)
    expect(r.top).toBeCloseTo(0)
    expect(r.width).toBeCloseTo(20)
    expect(r.height).toBeCloseTo(100)
  })

  it('scales linearly with zoom', () => {
    const at1 = boxToCss(TITLE, testViewport({ viewBox: [0, 0, 612, 792], scale: 1 }))
    const at2 = boxToCss(TITLE, testViewport({ viewBox: [0, 0, 612, 792], scale: 2 }))
    expect(at2.left).toBeCloseTo(at1.left * 2)
    expect(at2.height).toBeCloseTo(at1.height * 2)
  })
})

describe('boxToPercent and boxToPage', () => {
  it('express a box as a fraction of the page, so zooming never recomputes it', () => {
    const vp1 = testViewport({ viewBox: [0, 0, 612, 792] })
    const pct = boxToPercent({ x0: 61.2, y0: 79.2, x1: 306, y1: 396 }, vp1)
    expect(pct).toEqual({ left: '10%', top: '10%', width: '40%', height: '40%' })
    expect(boxToPage({ x0: 61.2, y0: 79.2, x1: 306, y1: 396 }, vp1)).toEqual({ x0: 61.2, y0: 79.2, x1: 306, y1: 396 })
  })
})

describe('cssToBox', () => {
  it.each([
    ['unrotated', 0],
    ['rotated 90', 90],
    ['rotated 180', 180],
    ['rotated 270', 270],
  ] as const)('round-trips a server box through the screen (%s)', (_name, rotation) => {
    const vp = testViewport({ viewBox: [0, 0, 612, 792], scale: 1.37, rotation })
    const back = cssToBox(boxToCss(TITLE, vp), vp, 0)
    expect(back.page).toBe(0)
    expect(back.x0).toBeCloseTo(TITLE.x0)
    expect(back.y0).toBeCloseTo(TITLE.y0)
    expect(back.x1).toBeCloseTo(TITLE.x1)
    expect(back.y1).toBeCloseTo(TITLE.y1)
  })

  it('round-trips with an offset CropBox', () => {
    const vp = testViewport({ viewBox: [18, 24, 594, 768], scale: 0.85 })
    const box = { page: 3, x0: 40, y0: 300, x1: 420, y1: 318 }
    const back = cssToBox(boxToCss(box, vp), vp, 3)
    expect(back.x0).toBeCloseTo(box.x0)
    expect(back.y1).toBeCloseTo(box.y1)
  })
})
