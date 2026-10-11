/**
 * The PDF column (DESIGN.md §4.4, §5.8): every page laid out at the current
 * zoom, canvases and text layers only near the viewport, the assistant's
 * brackets on cited passages, and the reader's selection reported to the store.
 *
 * It performs the store's scroll requests (citation jumps, outline clicks,
 * "Back to p. 11", restore on open) and reports the page in view.
 */
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useResolvedTheme } from '../../../app/theme'
import { usePreferences } from '../../../lib/preferences'
import { prefersReducedMotion } from '../../../lib/motion'
import { passagesOnPage, sectionAtPage, useReaderStore, type PassageMark } from '../../shared/readerStore'
import { boxToCss } from './coords'
import { PdfPage, type Flash } from './PdfPage'
import { NIGHT_PAGE_COLORS, openPdf, PDF_TO_CSS, type PDFDocumentProxy, type PageViewport } from './pdfjs'
import { readPdfSelection, type ReadSelection } from './selection'
import './pdf.css'

const NONE: readonly PassageMark[] = []

/** A page's top inside the scroller's content. Pages sit in positioned lane boxes, so offsetTop is not enough. */
const topIn = (root: HTMLElement, el: HTMLElement): number =>
  el.getBoundingClientRect().top - root.getBoundingClientRect().top + root.scrollTop
const SIDE_PADDING = 24
/** §5.15: a citation lands 30% from the top of the viewport. */
const LAND_AT = 0.3

export interface PdfViewerProps {
  url: string
  /** The reader selected text (null when the selection collapsed). Client rects place the popover. */
  onSelection: (selection: ReadSelection | null) => void
  onLoaded?: (numPages: number) => void
  onError?: (message: string) => void
  /** Bumped by "Try again" to reload the document. */
  reloadKey?: number
}

export function PdfViewer({ url, onSelection, onLoaded, onError, reloadKey = 0 }: PdfViewerProps) {
  const scrollerRef = useRef<HTMLDivElement>(null)
  const pageEls = useRef(new Map<number, HTMLDivElement>())
  const [doc, setDoc] = useState<PDFDocumentProxy | null>(null)
  const [viewports, setViewports] = useState<PageViewport[]>([])
  const [active, setActive] = useState<Set<number>>(() => new Set())
  const [width, setWidth] = useState(0)
  const [height, setHeight] = useState(0)
  const [laneWidth, setLaneWidth] = useState(32)
  const [flash, setFlash] = useState<Flash | null>(null)

  const zoom = useReaderStore((s) => s.zoom)
  const passages = useReaderStore((s) => s.passages)
  const scrollRequest = useReaderStore((s) => s.scrollRequest)
  const theme = useResolvedTheme()
  const pageMode = usePreferences((s) => s.pageMode)
  const mode = theme === 'light' ? 'paper' : pageMode
  const pageColors = mode === 'night' ? NIGHT_PAGE_COLORS : null

  // ---- load the document and every page's scale-1 viewport (about 1 ms a page)
  useEffect(() => {
    const task = openPdf(url)
    let alive = true
    task.promise.then(
      async (d) => {
        const vps = await Promise.all(Array.from({ length: d.numPages }, async (_, i) => (await d.getPage(i + 1)).getViewport({ scale: 1 })))
        if (!alive) return
        setDoc(d)
        setViewports(vps)
        onLoaded?.(d.numPages)
      },
      (e: Error) => {
        if (alive) onError?.(e.message)
      },
    )
    return () => {
      alive = false
      setDoc(null)
      void task.destroy()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url, reloadKey])

  // ---- the column's size, for the fit modes
  useLayoutEffect(() => {
    const el = scrollerRef.current
    if (!el) return
    const ro = new ResizeObserver(() => {
      setWidth(el.clientWidth)
      setHeight(el.clientHeight)
      setLaneWidth(parseFloat(getComputedStyle(el).getPropertyValue('--lane-w')) || 32)
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  // ---- scale: fit width (capped at --page-fit-max), fit page, or a zoom step
  const scale = useMemo(() => {
    if (!viewports.length || !width) return 1
    const widest = Math.max(...viewports.map((v) => v.width))
    const tallest = Math.max(...viewports.map((v) => v.height))
    const fitWidth = Math.min(width - 2 * SIDE_PADDING - 2 * laneWidth, 920) / widest
    if (zoom === 'fit-width') return Math.max(0.25, fitWidth)
    if (zoom === 'fit-page') return Math.max(0.25, Math.min(fitWidth, (height - 48) / tallest))
    return zoom * PDF_TO_CSS
  }, [viewports, width, height, laneWidth, zoom])

  useEffect(() => {
    useReaderStore.getState().setEffectiveScale(scale / PDF_TO_CSS)
  }, [scale])

  // ---- windowing: canvases and text layers only within ~1.5 screens
  useEffect(() => {
    const root = scrollerRef.current
    if (!root || !doc) return
    const io = new IntersectionObserver(
      (entries) => {
        setActive((prev) => {
          const next = new Set(prev)
          for (const e of entries) {
            const n = Number((e.target as HTMLElement).dataset.pageIndex)
            if (e.isIntersecting) next.add(n)
            else next.delete(n)
          }
          return next
        })
      },
      { root, rootMargin: '150% 0px' },
    )
    pageEls.current.forEach((el) => io.observe(el))
    return () => io.disconnect()
  }, [doc, viewports])

  // ---- reading position: which page is at 30% of the viewport, and how far down it
  const anchor = useRef({ page: 0, offset: 0 })
  const settle = useRef(0)
  const onScroll = useCallback(() => {
    const root = scrollerRef.current
    if (!root) return
    const probe = root.scrollTop + root.clientHeight * LAND_AT
    for (const [i, el] of pageEls.current) {
      const top = topIn(root, el)
      if (top <= probe && top + el.offsetHeight + 16 > probe) {
        anchor.current = { page: i, offset: Math.min(1, Math.max(0, (root.scrollTop - top) / el.offsetHeight)) }
        break
      }
    }
    window.clearTimeout(settle.current)
    settle.current = window.setTimeout(() => {
      const { page, offset } = anchor.current
      const store = useReaderStore.getState()
      if (store.currentPage !== page || Math.abs(store.currentOffset - offset) > 0.02) store.setPosition(page, offset)
    }, 150)
  }, [])

  // Keep the same place under the reader's eye when the scale changes.
  const lastScale = useRef(scale)
  useLayoutEffect(() => {
    if (lastScale.current === scale) return
    lastScale.current = scale
    const root = scrollerRef.current
    const el = pageEls.current.get(anchor.current.page)
    if (root && el) root.scrollTop = topIn(root, el) + anchor.current.offset * el.offsetHeight
  }, [scale])

  // ---- perform scroll requests from the store
  useEffect(() => {
    const request = scrollRequest
    const root = scrollerRef.current
    if (!request || !root || !viewports.length) return
    const store = useReaderStore.getState()
    let target: number | null = null
    if (request.boxes?.length) {
      const box = request.boxes[0]!
      const el = pageEls.current.get(box.page)
      const vp = viewports[box.page]
      if (el && vp) target = topIn(root, el) + boxToCss(box, vp.clone({ scale })).top - root.clientHeight * LAND_AT
    } else if (request.position) {
      const el = pageEls.current.get(request.position.page)
      if (el) target = topIn(root, el) + request.position.offset * el.offsetHeight - (request.reason === 'outline' ? 24 : 16)
    }
    if (target === null) return
    const distance = Math.abs(target - root.scrollTop)
    const smooth = request.behaviour === 'smooth' || (request.behaviour === 'auto' && distance < 2 * root.clientHeight)
    root.scrollTo({ top: Math.max(0, target), behavior: smooth && !prefersReducedMotion() ? 'smooth' : 'auto' })
    if (request.chunkId) setFlash({ chunkId: request.chunkId, seq: request.seq })
    if (request.focusBracket) {
      window.setTimeout(() => root.querySelector<HTMLElement>('.pdf-bracket-hit')?.focus({ preventScroll: true }), 350)
    }
    store.ackScroll(request.seq)
  }, [scrollRequest, viewports, scale])

  // ---- selection → the popover (§5.12)
  useEffect(() => {
    const root = scrollerRef.current
    if (!root) return
    const viewportFor = (i: number) => viewports[i]?.clone({ scale }) ?? null
    const read = () =>
      window.setTimeout(() => {
        const sel = readPdfSelection(root, viewportFor)
        if (sel) {
          const meta = useReaderStore.getState().meta
          useReaderStore.getState().setSelection({
            text: sel.text,
            page: sel.page,
            rects: sel.rects,
            sectionNumber: sectionAtPage(meta, sel.page)?.number ?? null,
          })
        }
        onSelection(sel)
      }, 0)
    const onSelectionChange = () => {
      if (document.getSelection()?.isCollapsed) {
        useReaderStore.getState().setSelection(null)
        onSelection(null)
      }
    }
    const endSelecting = () => root.querySelectorAll('.textLayer.selecting').forEach((t) => t.classList.remove('selecting'))
    root.addEventListener('pointerup', read)
    root.addEventListener('keyup', read)
    document.addEventListener('pointerup', endSelecting)
    document.addEventListener('selectionchange', onSelectionChange)
    return () => {
      root.removeEventListener('pointerup', read)
      root.removeEventListener('keyup', read)
      document.removeEventListener('pointerup', endSelecting)
      document.removeEventListener('selectionchange', onSelectionChange)
    }
  }, [viewports, scale, onSelection])

  const register = useCallback((i: number, el: HTMLDivElement | null) => {
    if (el) pageEls.current.set(i, el)
    else pageEls.current.delete(i)
  }, [])

  const perPage = useMemo(() => viewports.map((_, i) => passagesOnPage(passages, i)), [viewports, passages])

  return (
    <div
      className="pdf-viewer pdf-scroller"
      data-page-mode={mode}
      data-pdf-scroller
      ref={scrollerRef}
      onScroll={onScroll}
      tabIndex={-1}
      aria-label="Paper"
    >
      <div className="pdf-pages">
        {doc
          ? viewports.map((vp1, i) => (
              <PdfPage
                key={i}
                doc={doc}
                index={i}
                vp1={vp1}
                scale={scale}
                laneWidth={laneWidth}
                active={active.has(i)}
                passages={perPage[i]?.length ? perPage[i]! : NONE}
                flash={flash}
                pageColors={pageColors}
                register={register}
              />
            ))
          : null}
      </div>
    </div>
  )
}
