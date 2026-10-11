/**
 * A faithful copy of pdf.js's PageViewport transform (pdfjs-dist 6.4.299,
 * src/display/display_utils.js), for unit tests. pdf.js doesn't export the
 * class, and loading the library under jsdom pulls in its worker and canvas,
 * so the tests use this instead. If pdf.js changes its viewport maths, the
 * Playwright screenshots of brackets landing on the cited lines catch it.
 */
import type { ViewportLike } from './coords'

export function testViewport({
  viewBox,
  scale = 1,
  rotation = 0,
}: {
  viewBox: [number, number, number, number]
  scale?: number
  rotation?: 0 | 90 | 180 | 270
}): ViewportLike & { transform: number[] } {
  const centerX = (viewBox[2] + viewBox[0]) / 2
  const centerY = (viewBox[3] + viewBox[1]) / 2
  const [a, b, c, d] = {
    0: [1, 0, 0, -1],
    90: [0, 1, 1, 0],
    180: [-1, 0, 0, 1],
    270: [0, -1, -1, 0],
  }[rotation] as [number, number, number, number]
  let offsetCanvasX: number
  let offsetCanvasY: number
  let width: number
  let height: number
  if (a === 0) {
    offsetCanvasX = Math.abs(centerY - viewBox[1]) * scale
    offsetCanvasY = Math.abs(centerX - viewBox[0]) * scale
    width = (viewBox[3] - viewBox[1]) * scale
    height = (viewBox[2] - viewBox[0]) * scale
  } else {
    offsetCanvasX = Math.abs(centerX - viewBox[0]) * scale
    offsetCanvasY = Math.abs(centerY - viewBox[1]) * scale
    width = (viewBox[2] - viewBox[0]) * scale
    height = (viewBox[3] - viewBox[1]) * scale
  }
  const t = [
    a * scale,
    b * scale,
    c * scale,
    d * scale,
    offsetCanvasX - a * scale * centerX - c * scale * centerY,
    offsetCanvasY - b * scale * centerX - d * scale * centerY,
  ] as const
  return {
    viewBox,
    width,
    height,
    scale,
    transform: [...t],
    convertToViewportPoint: (x, y) => [x * t[0] + y * t[2] + t[4], x * t[1] + y * t[3] + t[5]],
    convertToPdfPoint: (x, y) => {
      const det = t[0] * t[3] - t[1] * t[2]
      return [
        (x * t[3] - y * t[2] + t[2] * t[5] - t[4] * t[3]) / det,
        (-x * t[1] + y * t[0] + t[4] * t[1] - t[5] * t[0]) / det,
      ]
    },
  }
}
