/**
 * Text lines and columns on one page (DESIGN.md §5.8 "Column detection").
 *
 * Input: the pdf.js text-layer spans of a page, measured once in page space
 * (CSS px at scale 1, see ./coords.ts). Output: line boxes, and whether the
 * page is set in two columns. The bracket layout (./brackets.ts) uses both to
 * put each bracket in the margin nearest the cited lines.
 */
import type { Rect } from './coords'

export type Line = Rect

export interface Columns {
  twoColumn: boolean
  /** 5th percentile of line left edges. */
  left: number
  /** 95th percentile of line right edges. */
  right: number
  mid: number
}

const height = (r: Rect) => r.y1 - r.y0
const width = (r: Rect) => r.x1 - r.x0
const overlapY = (a: Rect, b: Rect) => Math.min(a.y1, b.y1) - Math.max(a.y0, b.y0)

export function percentile(values: readonly number[], p: number): number {
  if (!values.length) return 0
  const sorted = [...values].sort((a, b) => a - b)
  const i = Math.min(sorted.length - 1, Math.max(0, Math.round(p * (sorted.length - 1))))
  return sorted[i]!
}

export function median(values: readonly number[]): number {
  return percentile(values, 0.5)
}

interface Row {
  /** y-range of the tallest span so far: superscripts join a row without stretching what it matches. */
  core: Rect
  spans: Rect[]
}

/**
 * Group spans into lines. Spans share a row when they overlap vertically by
 * at least 40% of the smaller height (so super- and subscripts stay with
 * their line). A row splits into separate lines across a horizontal gap wider
 * than the gutter test (max(8 pt, 1.2 × the row's height)), which keeps the
 * two columns of a two-column page apart.
 */
export function groupLines(spans: readonly Rect[]): Line[] {
  const valid = spans.filter((s) => width(s) > 0.5 && height(s) > 0.5)
  const sorted = [...valid].sort((a, b) => a.y0 + a.y1 - (b.y0 + b.y1) || a.x0 - b.x0)
  const rows: Row[] = []
  for (const s of sorted) {
    // Only recent rows can match: spans arrive in vertical order.
    let row: Row | undefined
    for (let i = rows.length - 1; i >= Math.max(0, rows.length - 4); i--) {
      const r = rows[i]!
      if (overlapY(r.core, s) >= 0.4 * Math.min(height(r.core), height(s))) {
        row = r
        break
      }
    }
    if (!row) {
      rows.push({ core: { ...s }, spans: [s] })
      continue
    }
    row.spans.push(s)
    if (height(s) > height(row.core)) row.core = { ...s }
  }

  const lines: Line[] = []
  for (const row of rows) {
    const byX = [...row.spans].sort((a, b) => a.x0 - b.x0)
    const gap = Math.max(8, 1.2 * height(row.core))
    let current: Line | null = null
    for (const s of byX) {
      if (current && s.x0 - current.x1 <= gap) {
        current.x0 = Math.min(current.x0, s.x0)
        current.x1 = Math.max(current.x1, s.x1)
        current.y0 = Math.min(current.y0, s.y0)
        current.y1 = Math.max(current.y1, s.y1)
      } else {
        if (current) lines.push(current)
        current = { ...s }
      }
    }
    if (current) lines.push(current)
  }
  return lines.sort((a, b) => a.y0 - b.y0 || a.x0 - b.x0)
}

/**
 * §5.8: textLeft and textRight are the 5th and 95th percentiles of the line
 * extents. The page is two-column when at least 60% of lines are narrower
 * than 0.6 × (textRight − textLeft) and those lines fall on both sides of the
 * middle with a gap of at least 8 pt between them.
 */
export function detectColumns(lines: readonly Line[]): Columns {
  const left = percentile(lines.map((l) => l.x0), 0.05)
  const right = percentile(lines.map((l) => l.x1), 0.95)
  const mid = (left + right) / 2
  const single: Columns = { twoColumn: false, left, right, mid }
  if (lines.length < 6 || right <= left) return single

  const span = right - left
  const narrow = lines.filter((l) => width(l) < 0.6 * span)
  if (narrow.length < 0.6 * lines.length) return single

  const leftSide = narrow.filter((l) => (l.x0 + l.x1) / 2 < mid)
  const rightSide = narrow.filter((l) => (l.x0 + l.x1) / 2 >= mid)
  // A ragged single column (a reference list, short lines) has everything on one side.
  if (leftSide.length < 0.2 * narrow.length || rightSide.length < 0.2 * narrow.length) return single

  const gap = percentile(rightSide.map((l) => l.x0), 0.1) - percentile(leftSide.map((l) => l.x1), 0.9)
  return gap >= 8 ? { twoColumn: true, left, right, mid } : single
}

export type Side = 'left' | 'right'

/**
 * Which margin a line's bracket goes in (§5.8 "nearest-margin rule"): the
 * right lane on a single-column page and for full-width items; on a
 * two-column page, the side of the column the line sits in.
 */
export function sideOf(line: Line, cols: Columns): Side {
  if (!cols.twoColumn) return 'right'
  if (width(line) >= 0.6 * (cols.right - cols.left)) return 'right'
  return (line.x0 + line.x1) / 2 < cols.mid ? 'left' : 'right'
}
