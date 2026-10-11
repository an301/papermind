/**
 * Converting between the server's geometry and the page on screen.
 *
 * The server (PyMuPDF) gives boxes in PDF points with a TOP-LEFT origin on
 * the UNROTATED page's CropBox (papermind/schemas/paper.py). pdf.js lays a
 * page out through a PageViewport, whose transform handles scale, the y-flip,
 * /Rotate and a CropBox that doesn't start at (0, 0). We undo PyMuPDF's flip
 * into PDF user space and let the viewport do the rest; never normalise by
 * PyMuPDF's page.rect, which is the *rotated* size.
 *
 * "Page space" below means the page as displayed at scale 1: CSS px divided
 * by the current scale, origin top-left, rotation applied. Everything the
 * bracket layout measures (text-layer lines, chunk boxes) lives in page space,
 * so zooming never recomputes it.
 */
import type { Box } from '../../../api/types'

/** The subset of pdf.js's PageViewport this module uses (so tests can supply their own). */
export interface ViewportLike {
  viewBox: number[]
  width: number
  height: number
  scale: number
  convertToViewportPoint(x: number, y: number): number[]
  convertToPdfPoint(x: number, y: number): number[]
}

/** A rectangle in page space or CSS px: left/top/right/bottom edges. */
export interface Rect {
  x0: number
  y0: number
  x1: number
  y1: number
}

export interface CssRect {
  left: number
  top: number
  width: number
  height: number
}

/**
 * A server box → CSS px inside a page rendered with `vp`. Works at any scale,
 * any rotation and with an offset CropBox. (pdf.js v6 removed
 * `convertToViewportRectangle`; this is its replacement.)
 */
export function boxToCss(b: Pick<Box, 'x0' | 'y0' | 'x1' | 'y1'>, vp: ViewportLike): CssRect {
  const vx0 = vp.viewBox[0] ?? 0
  const vy1 = vp.viewBox[3] ?? 0
  const [ax = 0, ay = 0] = vp.convertToViewportPoint(vx0 + b.x0, vy1 - b.y0)
  const [bx = 0, by = 0] = vp.convertToViewportPoint(vx0 + b.x1, vy1 - b.y1)
  return { left: Math.min(ax, bx), top: Math.min(ay, by), width: Math.abs(bx - ax), height: Math.abs(by - ay) }
}

/** A server box → page space (pass the scale-1 viewport). */
export function boxToPage(b: Pick<Box, 'x0' | 'y0' | 'x1' | 'y1'>, vp1: ViewportLike): Rect {
  const r = boxToCss(b, vp1)
  // Round off the transform's float noise (79.20000000000005 → 79.2) so equal boxes compare equal.
  const q = (v: number) => Math.round(v * 1e6) / 1e6
  return { x0: q(r.left), y0: q(r.top), x1: q(r.left + r.width), y1: q(r.top + r.height) }
}

/**
 * A server box as percentages of the page box: zoom-independent, so marks
 * drawn this way never need recomputing when the reader zooms.
 */
export function boxToPercent(b: Pick<Box, 'x0' | 'y0' | 'x1' | 'y1'>, vp1: ViewportLike): { left: string; top: string; width: string; height: string } {
  const r = boxToCss(b, vp1)
  const pct = (v: number, of: number) => `${of ? Math.round((v / of) * 1e6) / 1e4 : 0}%`
  return { left: pct(r.left, vp1.width), top: pct(r.top, vp1.height), width: pct(r.width, vp1.width), height: pct(r.height, vp1.height) }
}

/** The inverse: CSS px inside a page rendered with `vp` → a server box on that page (for selections). */
export function cssToBox(r: CssRect, vp: ViewportLike, page: number): Box {
  const vx0 = vp.viewBox[0] ?? 0
  const vy1 = vp.viewBox[3] ?? 0
  const [ax = 0, ay = 0] = vp.convertToPdfPoint(r.left, r.top)
  const [bx = 0, by = 0] = vp.convertToPdfPoint(r.left + r.width, r.top + r.height)
  const xs = [ax - vx0, bx - vx0]
  const ys = [vy1 - ay, vy1 - by]
  return { page, x0: Math.min(...xs), y0: Math.min(...ys), x1: Math.max(...xs), y1: Math.max(...ys) }
}

/** Round a box to 0.1 pt so stored marks stay small and compare cleanly. */
export const roundBox = (b: Box): Box => ({
  page: b.page,
  x0: Math.round(b.x0 * 10) / 10,
  y0: Math.round(b.y0 * 10) / 10,
  x1: Math.round(b.x1 * 10) / 10,
  y1: Math.round(b.y1 * 10) / 10,
})
