/**
 * Reading a text selection out of the pdf.js text layer (DESIGN.md §5.12).
 *
 * The DOM selection gives one client rect per text run, often several per
 * line and overlapping. We keep the rects that sit on each page the range
 * touches, merge them line by line, and convert them back to the server's
 * geometry (PDF points, top-left, unrotated) so a quote or a highlight can be
 * stored and redrawn at any zoom.
 */
import type { Box } from '../../../api/types'
import { cssToBox, roundBox, type CssRect, type ViewportLike } from './coords'

export interface PageHit {
  /** 0-based page index. */
  index: number
  /** The page element's client rect. */
  rect: Pick<DOMRect, 'left' | 'top' | 'right' | 'bottom'>
  /** The viewport the page is laid out with right now. */
  vp: ViewportLike
}

export interface ReadSelection {
  text: string
  /** The page the selection starts on (the quote's page). */
  page: number
  /** Merged line rects in server geometry, in reading order; each carries its page. */
  rects: Box[]
  /** Client rects of the first and last merged lines, for placing the popover. */
  first: CssRect
  last: CssRect
  /** Client bounding rect of the whole selection. */
  bounds: CssRect
}

/** Selected text as one line of prose: line breaks become spaces, "atten-⏎tion" keeps its hyphen without the break. */
export function normaliseSelectedText(raw: string): string {
  return raw
    .replace(/-\s*\n\s*/g, '-')
    .replace(/\s+/g, ' ')
    .trim()
}

/** §5.12: a selection opens the toolbar only with at least two non-space characters. */
export const isMeaningfulSelection = (text: string): boolean => text.replace(/\s/g, '').length >= 2

/**
 * Merge the rects of one line into one band. Rects belong to the same line
 * when their vertical centres are within half the smaller height of each
 * other and they touch or nearly touch horizontally.
 */
export function mergeLineRects(rects: readonly CssRect[]): CssRect[] {
  const sorted = [...rects]
    .filter((r) => r.width > 0.5 && r.height > 0.5)
    .sort((a, b) => a.top + a.height / 2 - (b.top + b.height / 2))
  // First group runs into lines by vertical centre, then merge neighbours left to right:
  // a run that only touches the line's middle must not split it into two bands.
  const lines: CssRect[][] = []
  for (const r of sorted) {
    const line = lines.find((l) =>
      l.some((p) => Math.abs(p.top + p.height / 2 - (r.top + r.height / 2)) < Math.min(p.height, r.height) / 2),
    )
    if (line) line.push(r)
    else lines.push([r])
  }
  const out: CssRect[] = []
  for (const line of lines) {
    let band: CssRect | null = null
    for (const r of [...line].sort((a, b) => a.left - b.left)) {
      if (band && r.left <= band.left + band.width + 12) {
        const right = Math.max(band.left + band.width, r.left + r.width)
        const bottom = Math.max(band.top + band.height, r.top + r.height)
        band.top = Math.min(band.top, r.top)
        band.width = right - band.left
        band.height = bottom - band.top
      } else {
        if (band) out.push(band)
        band = { ...r }
      }
    }
    if (band) out.push(band)
  }
  return out
}

/**
 * Client rects → merged line rects per page, in server geometry. Pure, so the
 * mapping is testable without a browser layout engine.
 */
export function clientRectsToBoxes(clientRects: readonly CssRect[], pages: readonly PageHit[]): { boxes: Box[]; lines: CssRect[] } {
  const boxes: Box[] = []
  const lines: CssRect[] = []
  for (const p of [...pages].sort((a, b) => a.index - b.index)) {
    const onPage = clientRects
      .filter((r) => {
        const cy = r.top + r.height / 2
        const cx = r.left + r.width / 2
        return cy >= p.rect.top && cy <= p.rect.bottom && cx >= p.rect.left - 1 && cx <= p.rect.right + 1
      })
      .map((r) => ({ left: r.left - p.rect.left, top: r.top - p.rect.top, width: r.width, height: r.height }))
    for (const line of mergeLineRects(onPage)) {
      boxes.push(roundBox(cssToBox(line, p.vp, p.index)))
      lines.push({ left: line.left + p.rect.left, top: line.top + p.rect.top, width: line.width, height: line.height })
    }
  }
  return { boxes, lines }
}

const bounding = (rs: readonly CssRect[]): CssRect => {
  const left = Math.min(...rs.map((r) => r.left))
  const top = Math.min(...rs.map((r) => r.top))
  const right = Math.max(...rs.map((r) => r.left + r.width))
  const bottom = Math.max(...rs.map((r) => r.top + r.height))
  return { left, top, width: right - left, height: bottom - top }
}

/**
 * Read the document selection if it lies inside `root` (the PDF scroller).
 * `pageAt(el)` resolves a `[data-page-index]` element to its index and the
 * viewport it is laid out with; pages without one are skipped.
 */
export function readPdfSelection(root: HTMLElement, viewportFor: (index: number) => ViewportLike | null): ReadSelection | null {
  const sel = document.getSelection()
  if (!sel || sel.isCollapsed || sel.rangeCount === 0) return null
  const range = sel.getRangeAt(0)
  if (!root.contains(range.commonAncestorContainer)) return null
  const text = normaliseSelectedText(sel.toString())
  if (!isMeaningfulSelection(text)) return null

  const pages: PageHit[] = []
  for (const el of root.querySelectorAll<HTMLElement>('[data-page-index]')) {
    if (!range.intersectsNode(el)) continue
    const index = Number(el.dataset.pageIndex)
    const vp = viewportFor(index)
    if (!vp) continue
    pages.push({ index, rect: el.getBoundingClientRect(), vp })
  }
  if (!pages.length) return null

  // Only rects inside the text layer: pdf.js's endOfContent sink can report a page-wide rect.
  const client = Array.from(range.getClientRects())
    .filter((r) => r.width > 0.5 && r.height > 0.5)
    .map((r) => ({ left: r.left, top: r.top, width: r.width, height: r.height }))
  const { boxes, lines } = clientRectsToBoxes(client, pages)
  const first = lines[0]
  const last = lines[lines.length - 1]
  if (!first || !last || !boxes[0]) return null
  return { text, page: boxes[0].page, rects: boxes, first, last, bounds: bounding(lines) }
}
