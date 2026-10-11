import { describe, expect, it } from 'vitest'
import type { Rect } from './coords'
import { assignLanes, citationLines, layoutBrackets, numeralLabel, stackNumerals, strokeX, taperedStrokePath, type Segment } from './brackets'
import { detectColumns, groupLines, sideOf, type Line } from './lines'

/** Lines of one column of body text: 10 pt type on a 12 pt grid. */
function column(x0: number, x1: number, from: number, count: number): Line[] {
  return Array.from({ length: count }, (_, i) => ({ x0, x1, y0: from + i * 12, y1: from + i * 12 + 10 }))
}

/** A single-column page like Attention Is All You Need (text 108–504 pt). */
const SINGLE = column(108, 504, 72, 50)
/** A two-column page: columns 54–300 and 312–558, a full-width title on top. */
const TWO = [{ x0: 120, x1: 492, y0: 60, y1: 76 }, ...column(54, 300, 100, 50), ...column(312, 558, 100, 50)]

describe('groupLines', () => {
  it('joins the runs of a line, keeps a superscript with its line, and keeps columns apart', () => {
    const spans: Rect[] = [
      // line 1, left column: two runs and a superscript
      { x0: 54, x1: 150, y0: 100, y1: 110 },
      { x0: 152, x1: 200, y0: 100, y1: 110 },
      { x0: 201, x1: 205, y0: 97, y1: 103 },
      // line 1, right column across a 12 pt gutter
      { x0: 312, x1: 550, y0: 100, y1: 110 },
      // line 2, left column
      { x0: 54, x1: 298, y0: 112, y1: 122 },
    ]
    const lines = groupLines(spans)
    expect(lines).toEqual([
      { x0: 54, x1: 205, y0: 97, y1: 110 },
      { x0: 312, x1: 550, y0: 100, y1: 110 },
      { x0: 54, x1: 298, y0: 112, y1: 122 },
    ])
  })

  it('ignores empty spans', () => {
    expect(groupLines([{ x0: 10, x1: 10, y0: 10, y1: 20 }])).toEqual([])
  })
})

describe('detectColumns', () => {
  it('reads a single-column page as one column', () => {
    const cols = detectColumns(SINGLE)
    expect(cols.twoColumn).toBe(false)
    expect(cols.left).toBe(108)
    expect(cols.right).toBe(504)
  })

  it('finds two columns when most lines are narrow and split around the middle', () => {
    const cols = detectColumns(TWO)
    expect(cols.twoColumn).toBe(true)
    expect(cols.mid).toBeCloseTo((54 + 558) / 2)
  })

  it('does not mistake a ragged single column (short lines all on the left) for two columns', () => {
    const ragged = column(108, 250, 72, 40)
    expect(detectColumns([...ragged, ...column(108, 504, 600, 4)]).twoColumn).toBe(false)
  })

  it('puts full-width lines and single-column pages in the right margin, and each column in its own', () => {
    const cols = detectColumns(TWO)
    expect(sideOf(TWO[0]!, cols)).toBe('right')
    expect(sideOf({ x0: 54, x1: 300, y0: 100, y1: 110 }, cols)).toBe('left')
    expect(sideOf({ x0: 312, x1: 558, y0: 100, y1: 110 }, cols)).toBe('right')
    expect(sideOf(SINGLE[0]!, detectColumns(SINGLE))).toBe('right')
  })
})

describe('citationLines', () => {
  it('takes the lines whose centres fall inside the boxes', () => {
    const lines = citationLines([{ x0: 100, y0: 130, x1: 510, y1: 170 }], SINGLE)
    expect(lines.map((l) => l.y0)).toEqual([132, 144, 156])
  })

  it('treats a box with no text lines in it (a figure, display maths) as one line', () => {
    const figure = { x0: 150, y0: 900, x1: 450, y1: 1000 }
    expect(citationLines([figure], SINGLE)).toEqual([figure])
  })
})

describe('layoutBrackets', () => {
  const single = detectColumns(SINGLE)
  const two = detectColumns(TWO)
  const cite = (key: string, numeral: number, boxes: Rect[], before = false, after = false) => ({
    key,
    numeral,
    boxes,
    continuesBefore: before,
    continuesAfter: after,
  })

  it('draws one bracket in the right margin of a single-column page, spanning the cited lines', () => {
    const [seg, ...rest] = layoutBrackets([cite('a', 1, [{ x0: 100, y0: 130, x1: 510, y1: 170 }])], SINGLE, single)
    expect(rest).toHaveLength(0)
    expect(seg).toMatchObject({ side: 'right', edge: 504, y0: 132, y1: 166, lane: 0, openTop: false, openBottom: false, numerals: [1] })
  })

  it('splits a citation that runs from the bottom of the left column to the top of the right one, with open ends between', () => {
    const segs = layoutBrackets(
      [
        cite('a', 2, [
          { x0: 50, y0: 680, x1: 302, y1: 700 },
          { x0: 310, y0: 98, x1: 560, y1: 126 },
        ]),
      ],
      TWO,
      two,
    )
    expect(segs).toHaveLength(2)
    const [left, right] = segs
    expect(left).toMatchObject({ side: 'left', edge: 54, openTop: false, openBottom: true })
    expect(right).toMatchObject({ side: 'right', edge: 558, openTop: true, openBottom: false })
    expect(left!.numerals).toEqual([2])
    expect(right!.numerals).toEqual([2])
  })

  it('leaves the ends open where a passage continues on the previous or next page', () => {
    const [seg] = layoutBrackets([cite('a', 1, [{ x0: 100, y0: 60, x1: 510, y1: 100 }], true, true)], SINGLE, single)
    expect(seg).toMatchObject({ openTop: true, openBottom: true })
  })

  it('starts a new segment across a gap of more than 1.5 line-heights', () => {
    const segs = layoutBrackets(
      [
        cite('a', 1, [
          { x0: 100, y0: 130, x1: 510, y1: 158 },
          { x0: 100, y0: 200, x1: 510, y1: 230 },
        ]),
      ],
      SINGLE,
      single,
    )
    expect(segs).toHaveLength(2)
    expect(segs[0]).toMatchObject({ openBottom: true })
    expect(segs[1]).toMatchObject({ openTop: true })
  })

  it('stacks overlapping citations in lanes, lane 0 nearest the text', () => {
    const segs = layoutBrackets(
      [cite('a', 1, [{ x0: 100, y0: 130, x1: 510, y1: 200 }]), cite('b', 2, [{ x0: 100, y0: 160, x1: 510, y1: 240 }])],
      SINGLE,
      single,
    )
    expect(segs.map((s) => [s.numerals[0], s.lane])).toEqual([
      [1, 0],
      [2, 1],
    ])
    expect(strokeX(segs[0]!, 1)).toBe(514)
    expect(strokeX(segs[1]!, 1)).toBe(520)
    expect(strokeX({ side: 'left', edge: 54, lane: 2 }, 2)).toBe(54 * 2 - 22)
  })

  it('reuses a lane once the bracket above has ended', () => {
    const segs = layoutBrackets(
      [cite('a', 1, [{ x0: 100, y0: 130, x1: 510, y1: 160 }]), cite('b', 2, [{ x0: 100, y0: 190, x1: 510, y1: 220 }])],
      SINGLE,
      single,
    )
    expect(segs.map((s) => s.lane)).toEqual([0, 0])
  })

  it('merges a cluster that would need a fourth lane into one bracket labelled with the range', () => {
    const box = (from: number) => [{ x0: 100, y0: from, x1: 510, y1: from + 80 }]
    const segs = layoutBrackets([cite('a', 1, box(130)), cite('b', 2, box(140)), cite('c', 3, box(150)), cite('d', 4, box(160))], SINGLE, single)
    expect(segs).toHaveLength(1)
    expect(segs[0]).toMatchObject({ lane: 0, numerals: [1, 2, 3, 4], keys: ['a', 'b', 'c', 'd'] })
    expect(segs[0]!.y0).toBe(132)
    expect(numeralLabel(segs[0]!.numerals)).toBe('1–4')
  })
})

describe('assignLanes', () => {
  it('keeps the two sides independent', () => {
    const seg = (side: 'left' | 'right', n: number): Segment => ({
      keys: [String(n)],
      numerals: [n],
      side,
      edge: 0,
      y0: 100,
      y1: 200,
      lines: [],
      openTop: false,
      openBottom: false,
      lane: 0,
    })
    expect(assignLanes([seg('left', 1), seg('right', 2)]).map((s) => s.lane)).toEqual([0, 0])
  })
})

describe('numeralLabel', () => {
  it('writes ranges with an en dash and gaps as a list', () => {
    expect(numeralLabel([3])).toBe('3')
    expect(numeralLabel([2, 1, 3])).toBe('1–3')
    expect(numeralLabel([1, 3, 4])).toBe('1, 3, 4')
    expect(numeralLabel([0])).toBe('')
  })
})

describe('stackNumerals', () => {
  it('moves a colliding numeral 13 px up, and again if it still collides', () => {
    // The third collides with the first, moves up, then collides with the lifted second and moves again.
    expect(stackNumerals([{ x: 100, y: 50 }, { x: 104, y: 50 }, { x: 107, y: 50 }])).toEqual([0, 13, 26])
    // Once the second has moved up, a third beside it at the original height is clear.
    expect(stackNumerals([{ x: 100, y: 50 }, { x: 106, y: 50 }, { x: 112, y: 52 }])).toEqual([0, 13, 0])
  })

  it('leaves numerals that are far enough apart', () => {
    expect(stackNumerals([{ x: 100, y: 50 }, { x: 100, y: 70 }, { x: 120, y: 50 }])).toEqual([0, 0, 0])
  })
})

describe('taperedStrokePath', () => {
  it('is 1.25 px wide at the ends and 1.75 px through the middle', () => {
    const d = taperedStrokePath(10, 0, 100)
    expect(d.startsWith('M9.38 0')).toBe(true)
    // The quadratic control sits at 2·(mid half-width) − end half-width = 1.125 px off the centre line.
    expect(d).toContain('Q8.88 50 9.38 100')
    expect(d).toContain('Q11.13 50 10.63 0')
  })
})
