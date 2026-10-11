/**
 * One PDF page (DESIGN.md §5.8): canvas, exact-span underlines, the pdf.js
 * text layer and the assistant's pencil brackets, inside a page box whose
 * side lanes hold the brackets at every zoom.
 *
 * The canvas and text layer exist only while the page is near the viewport;
 * the page element itself always stays, so selection and find never break.
 */
import { memo, useEffect, useMemo, useRef, useState } from 'react'
import { focusCitationChip } from '../../assistant/focusChip'
import type { PassageMark } from '../../shared/readerStore'
import { bracketPolyline, layoutBrackets, numeralLabel, strokeX, taperedStrokePath, type CitationOnPage, type Segment } from './brackets'
import { boxToPage, type Rect } from './coords'
import { detectColumns, groupLines, type Line } from './lines'
import { pdfjs, type PDFDocumentProxy, type PageViewport } from './pdfjs'

/** iOS Safari refuses canvases above 16.7 MP; it also caps memory per page. */
const MAX_CANVAS_PIXELS = 2 ** 24

export interface Flash {
  chunkId: string
  seq: number
}

export interface PdfPageProps {
  doc: PDFDocumentProxy
  /** 0-based. */
  index: number
  vp1: PageViewport
  scale: number
  laneWidth: number
  active: boolean
  passages: readonly PassageMark[]
  flash: Flash | null
  /** Dark theme "night page": pdf.js draws with these colours instead of filtering. */
  pageColors: { background: string; foreground: string } | null
  register: (index: number, el: HTMLDivElement | null) => void
}

const markKey = (p: Pick<PassageMark, 'messageId' | 'chunkId'>) => `${p.messageId ?? ''}|${p.chunkId}`

export const PdfPage = memo(function PdfPage({ doc, index, vp1, scale, laneWidth, active, passages, flash, pageColors, register }: PdfPageProps) {
  const canvasHost = useRef<HTMLDivElement>(null)
  const textHost = useRef<HTMLDivElement>(null)
  const pageEl = useRef<HTMLDivElement>(null)
  const textLayer = useRef<InstanceType<typeof pdfjs.TextLayer> | null>(null)
  const scaleRef = useRef(scale)
  scaleRef.current = scale
  const [lines, setLines] = useState<Line[] | null>(null)
  const [held, setHeld] = useState<string | null>(null)

  // Canvas, double-buffered: the old bitmap stays (stretched) until the new one is ready, so zoom never flashes white.
  useEffect(() => {
    const host = canvasHost.current
    if (!host) return
    if (!active) {
      releaseCanvases(host)
      return
    }
    let task: { cancel: () => void; promise: Promise<void> } | null = null
    let cancelled = false
    void doc.getPage(index + 1).then(async (page) => {
      if (cancelled) return
      const vp = page.getViewport({ scale })
      const dpr = Math.min(window.devicePixelRatio || 1, Math.sqrt(MAX_CANVAS_PIXELS / (vp.width * vp.height)))
      const canvas = document.createElement('canvas')
      canvas.width = Math.floor(vp.width * dpr)
      canvas.height = Math.floor(vp.height * dpr)
      task = page.render({
        canvas,
        viewport: vp,
        transform: dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : undefined,
        ...(pageColors ? { pageColors } : {}),
      })
      try {
        await task.promise
      } catch (e) {
        if (e instanceof pdfjs.RenderingCancelledException) return
        return
      }
      if (cancelled) return
      releaseCanvases(host)
      host.append(canvas)
    })
    return () => {
      cancelled = true
      task?.cancel()
    }
  }, [doc, index, scale, active, pageColors])

  // Text layer: built once per activation, re-laid-out on zoom. Lines are measured once, in page space.
  useEffect(() => {
    const host = textHost.current
    if (!host || !active) return
    let cancelled = false
    void doc.getPage(index + 1).then(async (page) => {
      if (cancelled) return
      const tl = new pdfjs.TextLayer({
        textContentSource: page.streamTextContent({ includeMarkedContent: true }),
        container: host,
        viewport: page.getViewport({ scale: scaleRef.current }),
      })
      textLayer.current = tl
      try {
        await tl.render()
      } catch {
        return
      }
      if (cancelled) return
      // pdf.js's sink: a drag that leaves a text run doesn't sweep up the whole page.
      const end = document.createElement('div')
      end.className = 'endOfContent'
      host.append(end)
      requestAnimationFrame(() => {
        if (cancelled || !pageEl.current) return
        setLines(measureLines(host, pageEl.current, scaleRef.current))
      })
    })
    return () => {
      cancelled = true
      textLayer.current?.cancel()
      textLayer.current = null
      host.replaceChildren()
    }
  }, [doc, index, active])

  useEffect(() => {
    textLayer.current?.update({ viewport: vp1.clone({ scale }) })
  }, [scale, vp1])

  // Brackets: the passages on this page, laid out against its text lines.
  const segments = useMemo<Segment[]>(() => {
    if (!lines || !passages.length) return []
    const citations: CitationOnPage[] = []
    for (const p of passages) {
      const here = p.boxes.filter((b) => b.page === index)
      if (!here.length) continue
      citations.push({
        key: markKey(p),
        numeral: p.numeral,
        boxes: here.map((b) => boxToPage(b, vp1)),
        continuesBefore: p.boxes.some((b) => b.page < index),
        continuesAfter: p.boxes.some((b) => b.page > index),
      })
    }
    return citations.length ? layoutBrackets(citations, lines, detectColumns(lines)) : []
  }, [lines, passages, index, vp1])

  const byKey = useMemo(() => new Map(passages.map((p) => [markKey(p), p])), [passages])
  const flashKeys = useMemo(() => {
    if (!flash) return new Set<string>()
    return new Set(passages.filter((p) => p.chunkId === flash.chunkId).map(markKey))
  }, [flash, passages])

  const width = vp1.width * scale
  const height = vp1.height * scale

  return (
    <div className="pdf-pagebox" style={{ paddingLeft: laneWidth, paddingRight: laneWidth }}>
      <div
        ref={(el) => {
          pageEl.current = el
          register(index, el)
        }}
        className="pdf-page"
        data-page-index={index}
        style={
          {
            width,
            height,
            '--scale-factor': scale,
            '--total-scale-factor': scale * (vp1.userUnit ?? 1),
          } as React.CSSProperties
        }
      >
        <div className="pdf-canvas" ref={canvasHost} />
        <div className="pdf-underlines" aria-hidden="true">
          {segments.flatMap((seg, si) => {
            const shown = seg.keys.some((k) => k === held || flashKeys.has(k))
            if (!shown) return []
            const flashing = !seg.keys.includes(held ?? '') && seg.keys.some((k) => flashKeys.has(k))
            return seg.lines.map((l, i) => (
              <span
                key={`${si}:${i}:${flashing ? flash?.seq : 'held'}`}
                className="pdf-underline"
                data-flash={flashing || undefined}
                style={{ left: l.x0 * scale, top: l.y1 * scale + 1, width: (l.x1 - l.x0) * scale }}
              />
            ))
          })}
        </div>
        <div className="textLayer" ref={textHost} onPointerDown={(e) => e.currentTarget.classList.add('selecting')} />
        {!active ? (
          <div className="pdf-page-number" aria-hidden="true">
            {index + 1}
          </div>
        ) : null}
      </div>

      {segments.length ? (
        <>
          <svg className="pdf-brackets" width={width + 2 * laneWidth} height={height} aria-hidden="true">
            {segments.map((seg, si) => {
              const x = laneWidth + strokeX(seg, scale)
              const top = seg.y0 * scale + 1
              const bottom = seg.y1 * scale - 1
              const tick = seg.side === 'right' ? -4 : 4
              const key = seg.keys.join('+')
              const inked = seg.keys.some((k) => flashKeys.has(k))
              return (
                <g key={`${key}:${si}`} className="pdf-bracket-draw">
                  <path className="pdf-bracket-stroke" d={taperedStrokePath(x, top, bottom)} />
                  {!seg.openTop ? <line className="pdf-bracket-tick" x1={x} y1={top} x2={x + tick} y2={top} /> : null}
                  {!seg.openBottom ? <line className="pdf-bracket-tick" x1={x} y1={bottom} x2={x + tick} y2={bottom} /> : null}
                  {inked ? (
                    <path key={`ink-${flash?.seq}`} className="pdf-bracket-ink" d={bracketPolyline(x, top, bottom, tick, seg.openTop, seg.openBottom)} />
                  ) : null}
                  {!seg.openTop ? (
                    <text className="pdf-bracket-numeral" x={x} y={top - 3}>
                      {numeralLabel(seg.numerals)}
                    </text>
                  ) : null}
                </g>
              )
            })}
          </svg>
          {segments.map((seg, si) => {
            const x = laneWidth + strokeX(seg, scale)
            const first = byKey.get(seg.keys[0]!)
            const where = first?.sectionNumber ? `section ${first.sectionNumber}, ` : ''
            const label = `Citation ${numeralLabel(seg.numerals)}, ${where}page ${index + 1}. Show the answer`
            return (
              <button
                key={`hit-${seg.keys.join('+')}:${si}`}
                type="button"
                className="pdf-bracket-hit"
                aria-label={label}
                style={{ left: x - 12, top: seg.y0 * scale - 8, width: 24, height: Math.max(24, (seg.y1 - seg.y0) * scale + 16) }}
                onPointerEnter={() => setHeld(seg.keys[0]!)}
                onPointerLeave={() => setHeld(null)}
                onFocus={() => setHeld(seg.keys[0]!)}
                onBlur={() => setHeld(null)}
                onClick={() => {
                  if (first) focusCitationChip(first.messageId, first.label)
                }}
              />
            )
          })}
        </>
      ) : null}
    </div>
  )
})

/** Text-layer spans → lines in page space (CSS px at the current scale ÷ scale). */
function measureLines(host: HTMLElement, page: HTMLElement, scale: number): Line[] {
  const origin = page.getBoundingClientRect()
  const spans: Rect[] = []
  for (const span of host.querySelectorAll<HTMLElement>('span')) {
    if (!span.textContent?.trim()) continue
    const r = span.getBoundingClientRect()
    if (r.width < 0.5 || r.height < 0.5) continue
    spans.push({
      x0: (r.left - origin.left) / scale,
      y0: (r.top - origin.top) / scale,
      x1: (r.right - origin.left) / scale,
      y1: (r.bottom - origin.top) / scale,
    })
  }
  return groupLines(spans)
}

function releaseCanvases(host: HTMLElement) {
  for (const c of host.querySelectorAll('canvas')) {
    // Frees GPU memory in Safari.
    c.width = 0
    c.height = 0
  }
  host.replaceChildren()
}
