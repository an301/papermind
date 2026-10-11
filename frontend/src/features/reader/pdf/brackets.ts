/**
 * Layout of the assistant's pencil brackets on one page (DESIGN.md §5.8).
 *
 * Pure functions over page space (./coords.ts), so they are tested without a
 * browser. The bracket layer (./BracketLayer.tsx) turns segments into px at
 * the current zoom.
 *
 * The rules, in order:
 * - A citation's lines are the text lines whose centres fall inside its
 *   chunk boxes. A box with no lines inside (a figure, a table, display math
 *   drawn as graphics) counts as one line spanning the whole box.
 * - Lines go to the margin nearest them (./lines.ts `sideOf`).
 * - A citation becomes one segment per contiguous run of lines in one
 *   (page, side); lines more than 1.5 line-heights apart start a new run.
 *   A segment that continues elsewhere has an open end: no tick there.
 * - Segments take lanes per side greedily by vertical overlap, lane 0
 *   nearest the text, at most 3. A cluster that would need a fourth lane
 *   merges into one bracket labelled "1–4" or "1, 3, 4".
 */
import type { Rect } from './coords'
import { median, sideOf, type Columns, type Line, type Side } from './lines'

export interface CitationOnPage {
  /** Unique per citation on the page (message id + chunk id). */
  key: string
  /** Chip and bracket numeral, 1–8; 0 when unknown. */
  numeral: number
  /** The chunk's boxes on this page, in page space. */
  boxes: readonly Rect[]
  /** The chunk also has boxes on an earlier page / a later page. */
  continuesBefore: boolean
  continuesAfter: boolean
}

export interface Segment {
  /** Citations drawn by this bracket: one, or several when a crowded cluster merged. */
  keys: string[]
  numerals: number[]
  side: Side
  /** The text edge the bracket hangs off, in page space: the right edge for the right lane, the left edge for the left lane. */
  edge: number
  y0: number
  y1: number
  /** The cited lines, for the exact-span underline. */
  lines: Line[]
  openTop: boolean
  openBottom: boolean
  lane: number
}

/** Max lanes per side before a cluster merges. */
export const MAX_LANES = 3
/** Brackets touching within this much (page space) still count as overlapping, so their ticks never meet. */
const SLACK = 2

const centre = (r: Rect) => ({ x: (r.x0 + r.x1) / 2, y: (r.y0 + r.y1) / 2 })
const inside = (p: { x: number; y: number }, b: Rect, tol = 1) =>
  p.x >= b.x0 - tol && p.x <= b.x1 + tol && p.y >= b.y0 - tol && p.y <= b.y1 + tol

/** The text lines a citation covers; a box with no text lines stands for itself. */
export function citationLines(boxes: readonly Rect[], lines: readonly Line[]): Line[] {
  const out: Line[] = []
  for (const box of boxes) {
    const hits = lines.filter((l) => inside(centre(l), box))
    if (hits.length) {
      for (const h of hits) if (!out.includes(h)) out.push(h)
    } else out.push({ ...box })
  }
  return out.sort((a, b) => a.y0 - b.y0 || a.x0 - b.x0)
}

/** Split one side's lines (sorted by y) into runs; a gap above 1.5 line-heights starts a new run. */
function runs(lines: readonly Line[], lineHeight: number): Line[][] {
  const out: Line[][] = []
  for (const l of lines) {
    const run = out[out.length - 1]
    const prev = run?.[run.length - 1]
    if (run && prev && l.y0 - prev.y1 <= 1.5 * lineHeight) run.push(l)
    else out.push([l])
  }
  return out
}

/** Segments for one citation on one page, in reading order (left column first). */
export function citationSegments(c: CitationOnPage, lines: readonly Line[], cols: Columns): Segment[] {
  const own = citationLines(c.boxes, lines)
  if (!own.length) return []
  const lineHeight = median(own.map((l) => l.y1 - l.y0)) || 10
  const bySide: Record<Side, Line[]> = { left: [], right: [] }
  for (const l of own) bySide[sideOf(l, cols)].push(l)

  const ordered: { side: Side; lines: Line[] }[] = []
  for (const side of ['left', 'right'] as const) {
    for (const run of runs(bySide[side], lineHeight)) ordered.push({ side, lines: run })
  }
  return ordered.map((r, i) => {
    const edge = r.side === 'right' ? Math.max(cols.right, ...r.lines.map((l) => l.x1)) : Math.min(cols.left, ...r.lines.map((l) => l.x0))
    return {
      keys: [c.key],
      numerals: [c.numeral],
      side: r.side,
      edge,
      y0: Math.min(...r.lines.map((l) => l.y0)),
      y1: Math.max(...r.lines.map((l) => l.y1)),
      lines: r.lines,
      openTop: i > 0 || c.continuesBefore,
      openBottom: i < ordered.length - 1 || c.continuesAfter,
      lane: 0,
    }
  })
}

/** Group segments of one side into clusters connected by vertical overlap. */
function clusters(segs: readonly Segment[]): Segment[][] {
  const sorted = [...segs].sort((a, b) => a.y0 - b.y0 || (a.numerals[0] ?? 0) - (b.numerals[0] ?? 0))
  const out: Segment[][] = []
  let bottom = -Infinity
  for (const s of sorted) {
    const current = out[out.length - 1]
    if (current && s.y0 < bottom + SLACK) {
      current.push(s)
      bottom = Math.max(bottom, s.y1)
    } else {
      out.push([s])
      bottom = s.y1
    }
  }
  return out
}

function merge(cluster: readonly Segment[]): Segment {
  const top = cluster.reduce((a, b) => (b.y0 < a.y0 ? b : a))
  const last = cluster.reduce((a, b) => (b.y1 > a.y1 ? b : a))
  const side = cluster[0]!.side
  const pairs = cluster.flatMap((s) => s.keys.map((k, i) => [s.numerals[i] ?? 0, k] as const))
  const unique = [...new Map(pairs.map(([n, k]) => [k, n])).entries()].sort((a, b) => a[1] - b[1])
  return {
    keys: unique.map(([k]) => k),
    numerals: unique.map(([, n]) => n),
    side,
    edge: side === 'right' ? Math.max(...cluster.map((s) => s.edge)) : Math.min(...cluster.map((s) => s.edge)),
    y0: top.y0,
    y1: last.y1,
    lines: cluster.flatMap((s) => s.lines),
    openTop: top.openTop,
    openBottom: last.openBottom,
    lane: 0,
  }
}

/** Assign lanes 0–2 within each overlapping cluster; a cluster needing a fourth merges into one bracket. */
export function assignLanes(segs: readonly Segment[]): Segment[] {
  const out: Segment[] = []
  for (const side of ['left', 'right'] as const) {
    for (const cluster of clusters(segs.filter((s) => s.side === side))) {
      const laneEnds: number[] = []
      const placed: Segment[] = []
      let overflow = false
      for (const s of cluster) {
        let lane = laneEnds.findIndex((end) => end + SLACK <= s.y0)
        if (lane < 0) {
          if (laneEnds.length >= MAX_LANES) {
            overflow = true
            break
          }
          lane = laneEnds.length
          laneEnds.push(s.y1)
        } else laneEnds[lane] = s.y1
        placed.push({ ...s, lane })
      }
      if (overflow) out.push(merge(cluster))
      else out.push(...placed)
    }
  }
  // Reading order within a citation: the left column before the right, then top to bottom.
  const sideRank = (seg: Segment) => (seg.side === 'left' ? 0 : 1)
  return out.sort((a, b) => (a.numerals[0] ?? 0) - (b.numerals[0] ?? 0) || sideRank(a) - sideRank(b) || a.y0 - b.y0)
}

/** Every bracket on a page. */
export function layoutBrackets(citations: readonly CitationOnPage[], lines: readonly Line[], cols: Columns): Segment[] {
  return assignLanes(citations.flatMap((c) => citationSegments(c, lines, cols)))
}

/** "2", "1–4" for a contiguous run, "1, 3, 4" otherwise (§5.8). */
export function numeralLabel(numerals: readonly number[]): string {
  const ns = [...new Set(numerals.filter((n) => n > 0))].sort((a, b) => a - b)
  if (!ns.length) return ''
  if (ns.length === 1) return String(ns[0])
  const contiguous = ns.every((n, i) => i === 0 || n === ns[i - 1]! + 1)
  return contiguous ? `${ns[0]}–${ns[ns.length - 1]}` : ns.join(', ')
}

/**
 * §5.8: two numerals within 8 px horizontally and 12 px vertically collide;
 * the later one moves 13 px up (repeatedly, until it is clear). Returns the
 * upward shift for each position, in the order given.
 */
export function stackNumerals(points: readonly { x: number; y: number }[]): number[] {
  const placed: { x: number; y: number }[] = []
  return points.map((p) => {
    let shift = 0
    while (placed.some((q) => Math.abs(q.x - p.x) < 8 && Math.abs(q.y - (p.y - shift)) < 12)) shift += 13
    placed.push({ x: p.x, y: p.y - shift })
    return shift
  })
}

// ---------------------------------------------------------------- drawing (px)

/** Stroke x of a bracket in px relative to the page: 10 px off the text edge, 6 px per lane (§5.8). */
export function strokeX(seg: Pick<Segment, 'side' | 'edge' | 'lane'>, scale: number): number {
  const off = 10 + 6 * seg.lane
  return seg.side === 'right' ? seg.edge * scale + off : seg.edge * scale - off
}

/**
 * The tapered stroke as a filled outline: 1.25 px wide at the ends and
 * 1.75 px through the middle, with round ends. Coordinates are px in the
 * drawing's own box.
 */
export function taperedStrokePath(x: number, top: number, bottom: number, endWidth = 1.25, midWidth = 1.75): string {
  const e = endWidth / 2
  // A quadratic edge through the half-width `midWidth / 2` at the midpoint needs its control at 2·mid − end.
  const c = midWidth - e
  const m = (top + bottom) / 2
  const f = (n: number) => Math.round(n * 100) / 100
  return [
    `M${f(x - e)} ${f(top)}`,
    `Q${f(x - c)} ${f(m)} ${f(x - e)} ${f(bottom)}`,
    `A${f(e)} ${f(e)} 0 0 0 ${f(x + e)} ${f(bottom)}`,
    `Q${f(x + c)} ${f(m)} ${f(x + e)} ${f(top)}`,
    `A${f(e)} ${f(e)} 0 0 0 ${f(x - e)} ${f(top)}`,
    'Z',
  ].join(' ')
}

/** The outline of a whole bracket (stroke and ticks) as one polyline, for the uniform re-ink and pinned strokes. */
export function bracketPolyline(x: number, top: number, bottom: number, tick: number, openTop: boolean, openBottom: boolean): string {
  const f = (n: number) => Math.round(n * 100) / 100
  const parts: string[] = []
  parts.push(openTop ? `M${f(x)} ${f(top)}` : `M${f(x + tick)} ${f(top)} L${f(x)} ${f(top)}`)
  parts.push(`L${f(x)} ${f(bottom)}`)
  if (!openBottom) parts.push(`L${f(x + tick)} ${f(bottom)}`)
  return parts.join(' ')
}
