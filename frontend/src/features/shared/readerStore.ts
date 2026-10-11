/**
 * The Reader's shared state: the one place the PDF viewer (src/features/reader)
 * and the assistant panel (src/features/assistant) meet. Who reads and writes
 * each field is in ./README.md; read it before adding a field.
 *
 * Rules:
 * - Server state (papers, sessions, chunks, knowledge) lives in TanStack Query
 *   (src/api/queries.ts). This store holds UI state and *copies* only what both
 *   sides need right now (`meta`, `session`).
 * - Requests that the other side must carry out (scroll the PDF, focus the
 *   composer, ask a question) are *request objects with a `seq`*: the side
 *   that performs them watches the field, acts once per `seq`, then calls the
 *   matching `ack…`/`consume…`. A request made before the performer mounts
 *   waits for it.
 * - Geometry is in PDF points, top-left origin, 0-based pages (the server's
 *   convention, see papermind/schemas/paper.py). The viewer converts to CSS.
 */
import { create } from 'zustand'
import { fetchChunk } from '../../api/queries'
import type { Box, ChunkDetail, Level, PaperMeta, Quote, Session, SourceRef } from '../../api/types'
import { storage } from '../../lib/storage'

// ---------------------------------------------------------------- types

/** What the reader selected in the PDF text layer. */
export interface TextSelection {
  text: string
  /** 0-based page the selection starts on (the quote's page, §5.12). */
  page: number
  /** Merged line rects, one per line per page; each Box carries its page. */
  rects: Box[]
  /** Number of the section the selection starts in ('3.2.1'), for the locator; null when unnumbered. */
  sectionNumber?: string | null
}

/** The quote chip in the composer (§5.20). `text` and `page` are what ChatRequest.quote sends. */
export interface ComposerQuote extends Quote {
  sectionNumber?: string | null
}

/** The citation the reader last jumped to (§5.15 "Active"). */
export interface ActiveCitation {
  chunkId: string
  /** Writer label, 'S2'. */
  label: string
  /** The assistant message the chip belongs to; null for a jump not tied to an answer. */
  messageId: string | null
}

/**
 * One cited passage to mark on the page (the brief's "highlights"). The
 * viewer draws it as the assistant's pencil bracket and transient underline
 * (§5.8): strokes only, never a fill. The reader's own highlighter fills are
 * a different thing and are not stored here.
 */
export interface PassageMark {
  chunkId: string
  /** Writer label, 'S2'. */
  label: string
  /** The numeral on the chip and the bracket: order of first appearance in the answer, 1–8. */
  numeral: number
  /** Block-level rects from ChunkDetail.boxes; empty until the chunk is fetched. */
  boxes: Box[]
  pageStart: number
  pageEnd: number
  sectionNumber: string | null
  messageId: string | null
  /** Pinned answers keep their brackets after the reader moves on (§1.2). */
  pinned: boolean
}

/** A place in the paper: 0-based page and how far down it (0..1). */
export interface PaperPosition {
  page: number
  offset: number
}

export type ScrollReason = 'citation' | 'outline' | 'page' | 'back' | 'restore'
/** 'auto' = the viewer decides (§6.3: smooth within 2 viewport heights, instant beyond, instant under reduced motion). */
export type ScrollBehaviour = 'auto' | 'smooth' | 'instant'

/**
 * Ask the viewer to scroll. Exactly one of `boxes` (put the first box 30%
 * from the top, §5.15) or `position` is set.
 */
export interface ScrollRequest {
  seq: number
  reason: ScrollReason
  behaviour: ScrollBehaviour
  boxes?: Box[]
  position?: PaperPosition
  /** For citation jumps: which passage to re-ink and underline (§6.3). */
  chunkId?: string
  /** Keyboard activation moves focus to the bracket button afterwards (§5.15 step 6). */
  focusBracket?: boolean
}

/** Ask the assistant to send a question (Explain from the selection popover, Recap, outline actions). */
export interface AskRequest {
  seq: number
  text: string
  quote: ComposerQuote | null
}

/** Zoom choice. Numbers are scales (1 = 100%); the viewer resolves the fit modes and reports `effectiveScale`. */
export type Zoom = 'fit-width' | 'fit-page' | number

/** §5.6 zoom steps. */
export const ZOOM_STEPS = [0.5, 0.67, 0.75, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2, 3] as const

/** The largest zoom step, as a plain number (indexing the tuple by length reads as possibly undefined). */
export const ZOOM_MAX = 3

export const PANEL_MIN = 340
export const PANEL_MAX = 560
export const PANEL_DEFAULT = 400
/** §5.8: the jump history keeps 20 entries. */
export const JUMP_HISTORY_MAX = 20

// ---------------------------------------------------------------- state

export interface ReaderState {
  // ---- the paper and conversation (set by the Reader when it opens a paper)
  paperId: string | null
  /** Present once the paper is ready. */
  meta: PaperMeta | null
  /** The assistant's session for this paper; the assistant panel owns creating it. */
  session: Session | null

  // ---- reading position (written by the viewer)
  /** Depth for the next question (§5.9). Mirrors ?depth= in the URL and Session.level. */
  level: Level
  /** 0-based page mostly in view. */
  currentPage: number
  /** Fraction down the current page, for scroll restore (PATCH /sessions scroll). */
  currentOffset: number
  zoom: Zoom
  /** The scale actually in use after resolving fit modes (for "110%"). */
  effectiveScale: number

  // ---- selection and the composer quote
  selection: TextSelection | null
  quote: ComposerQuote | null

  // ---- citations on the page
  activeCitation: ActiveCitation | null
  /** Passages to bracket: the active answer's citations plus pinned ones. */
  passages: PassageMark[]
  /** Chunk being fetched for a jump; the chip can show nothing (it's < 200 ms) or a busy state. */
  pendingChunkId: string | null
  jumpHistory: PaperPosition[]

  // ---- layout
  panelOpen: boolean
  /** Docked panel width in px, 340–560, remembered per device. */
  panelWidth: number
  outlineOpen: boolean

  // ---- cross-component requests (see the module comment)
  scrollRequest: ScrollRequest | null
  askRequest: AskRequest | null
  /** Bumped to ask the composer to take focus (A, /, Recap, "Ask about this paper"). */
  composerFocusSeq: number
}

export interface ReaderActions {
  /** Reset everything paper-specific. Call when /paper/:paperId changes. */
  openPaper: (paperId: string, init?: { meta?: PaperMeta | null; level?: Level }) => void
  closePaper: () => void
  setMeta: (meta: PaperMeta | null) => void
  setSession: (session: Session | null) => void
  setLevel: (level: Level) => void

  /** Viewer: the page in view changed (on scroll settle, not per frame). */
  setPosition: (page: number, offset?: number) => void
  setZoom: (zoom: Zoom) => void
  zoomIn: () => void
  zoomOut: () => void
  setEffectiveScale: (scale: number) => void

  setSelection: (selection: TextSelection | null) => void
  /** "Ask about this" (A): the selection becomes the composer's quote chip and the composer takes focus. */
  attachSelectionAsQuote: () => void
  setQuote: (quote: ComposerQuote | null) => void

  /**
   * Make an answer's citations the bracketed passages (pinned marks of other
   * answers stay). Pass the answer `text` so numerals follow first appearance;
   * pass `cited` (AnswerDone.cited) to skip sources the text never used.
   * Fetches each chunk's boxes (cached per paper).
   */
  showSources: (
    messageId: string | null,
    sources: readonly SourceRef[],
    options?: { cited?: readonly string[]; text?: string },
  ) => Promise<void>
  setPassagePinned: (chunkId: string, messageId: string | null, pinned: boolean) => void
  clearPassages: (options?: { keepPinned?: boolean }) => void
  setActiveCitation: (citation: ActiveCitation | null) => void

  /**
   * Jump to a passage (a chip click, "Show in paper"): fetch the chunk's boxes
   * (cached per paper), make sure it is marked, mark it active, remember where
   * the reader was (for "Back to p. 11"), then ask the viewer to scroll.
   * Resolves with the chunk, or null when no paper is open or a newer jump
   * superseded this one. Rejects when the chunk can't be fetched.
   */
  requestScrollToChunk: (
    chunkId: string,
    options?: { label?: string; messageId?: string | null; behaviour?: ScrollBehaviour; viaKeyboard?: boolean },
  ) => Promise<ChunkDetail | null>
  /** Outline clicks, the page input, restore on open. */
  requestScrollToPosition: (position: PaperPosition, reason?: ScrollReason, behaviour?: ScrollBehaviour) => void
  /** "Back to p. 11" / Alt+←. Returns false when there is nowhere to go back to. */
  jumpBack: () => boolean
  /** Viewer: the scroll for `seq` is done (or was dropped). */
  ackScroll: (seq: number) => void

  /** Ask the assistant to send a question (opens the panel). */
  requestAsk: (text: string, quote?: ComposerQuote | null) => void
  /** Assistant: the ask for `seq` was taken. */
  consumeAsk: (seq: number) => void
  requestComposerFocus: () => void

  setPanelOpen: (open: boolean) => void
  togglePanel: () => void
  setPanelWidth: (width: number) => void
  setOutlineOpen: (open: boolean) => void
  toggleOutline: () => void
}

export type ReaderStore = ReaderState & ReaderActions

// ---------------------------------------------------------------- persistence (per device)

const ZOOM_KEY = 'pm:zoom'
const PANEL_WIDTH_KEY = 'pm:panel-width'

export const clampPanelWidth = (w: number): number =>
  Math.round(Math.min(PANEL_MAX, Math.max(PANEL_MIN, Number.isFinite(w) ? w : PANEL_DEFAULT)))

function loadZoom(): Zoom {
  const raw = storage.get(ZOOM_KEY)
  if (raw === 'fit-width' || raw === 'fit-page') return raw
  const n = raw === null ? NaN : Number(raw)
  return Number.isFinite(n) && n >= ZOOM_STEPS[0] && n <= ZOOM_MAX ? n : 'fit-width'
}

function loadPanelWidth(): number {
  const raw = storage.get(PANEL_WIDTH_KEY)
  return raw === null ? PANEL_DEFAULT : clampPanelWidth(Number(raw))
}

/** Next zoom step above or below a scale (§5.6). */
export function stepZoom(scale: number, direction: 1 | -1): number {
  if (direction > 0) return ZOOM_STEPS.find((z) => z > scale + 0.001) ?? ZOOM_MAX
  return [...ZOOM_STEPS].reverse().find((z) => z < scale - 0.001) ?? ZOOM_STEPS[0]
}

// ---------------------------------------------------------------- helpers

/** Numerals by order of first appearance in the answer text (§5.15); labels never cited keep their source order after those. */
export function numeralsFor(sources: readonly SourceRef[], text?: string): Map<string, number> {
  const order: string[] = []
  if (text) for (const m of text.matchAll(/\[(S\d+)\]/g)) if (!order.includes(m[1]!)) order.push(m[1]!)
  for (const s of sources) if (!order.includes(s.label)) order.push(s.label)
  return new Map(order.map((label, i) => [label, i + 1]))
}

function markFromSource(source: SourceRef, numeral: number, messageId: string | null, pinned = false): PassageMark {
  return {
    chunkId: source.chunk_id,
    label: source.label,
    numeral,
    boxes: [],
    pageStart: source.page_start,
    pageEnd: source.page_end,
    sectionNumber: source.section_number ?? null,
    messageId,
    pinned,
  }
}

const sameMark = (a: Pick<PassageMark, 'chunkId' | 'messageId'>, b: Pick<PassageMark, 'chunkId' | 'messageId'>) =>
  a.chunkId === b.chunkId && a.messageId === b.messageId

// ---------------------------------------------------------------- store

let seq = 0
const nextSeq = () => ++seq
/** The latest jump; an older one that resolves late must not scroll. */
let latestJump = 0

const paperDefaults = (): Pick<
  ReaderState,
  | 'meta'
  | 'session'
  | 'currentPage'
  | 'currentOffset'
  | 'selection'
  | 'quote'
  | 'activeCitation'
  | 'passages'
  | 'pendingChunkId'
  | 'jumpHistory'
  | 'scrollRequest'
  | 'askRequest'
> => ({
  meta: null,
  session: null,
  currentPage: 0,
  currentOffset: 0,
  selection: null,
  quote: null,
  activeCitation: null,
  passages: [],
  pendingChunkId: null,
  jumpHistory: [],
  scrollRequest: null,
  askRequest: null,
})

export const useReaderStore = create<ReaderStore>()((set, get) => ({
  paperId: null,
  level: 'intermediate',
  zoom: loadZoom(),
  effectiveScale: 1,
  panelOpen: true,
  panelWidth: loadPanelWidth(),
  outlineOpen: false,
  composerFocusSeq: 0,
  ...paperDefaults(),

  openPaper: (paperId, init = {}) => {
    if (get().paperId === paperId) {
      set({ meta: init.meta ?? get().meta, ...(init.level ? { level: init.level } : {}) })
      return
    }
    latestJump = 0
    set({ ...paperDefaults(), paperId, meta: init.meta ?? null, ...(init.level ? { level: init.level } : {}) })
  },
  closePaper: () => {
    latestJump = 0
    set({ ...paperDefaults(), paperId: null })
  },
  setMeta: (meta) => set({ meta }),
  setSession: (session) => set(session ? { session, level: session.level } : { session }),
  setLevel: (level) => set({ level }),

  setPosition: (page, offset = 0) => set({ currentPage: page, currentOffset: Math.min(1, Math.max(0, offset)) }),
  setZoom: (zoom) => {
    storage.set(ZOOM_KEY, String(zoom))
    set({ zoom })
  },
  zoomIn: () => get().setZoom(stepZoom(get().effectiveScale, 1)),
  zoomOut: () => get().setZoom(stepZoom(get().effectiveScale, -1)),
  setEffectiveScale: (effectiveScale) => set({ effectiveScale }),

  setSelection: (selection) => set({ selection }),
  attachSelectionAsQuote: () => {
    const sel = get().selection
    if (!sel) return
    set((s) => ({
      quote: { text: sel.text, page: sel.page, sectionNumber: sel.sectionNumber ?? null },
      selection: null,
      composerFocusSeq: s.composerFocusSeq + 1,
      panelOpen: true,
    }))
  },
  setQuote: (quote) => set({ quote }),

  showSources: async (messageId, sources, { cited, text } = {}) => {
    const usable = cited?.length ? sources.filter((s) => cited.includes(s.label)) : [...sources]
    const numerals = numeralsFor(usable, text)
    const pinned = get().passages.filter((p) => p.pinned && p.messageId !== messageId)
    const marks = usable.map((s) => markFromSource(s, numerals.get(s.label) ?? 0, messageId))
    set({ passages: [...pinned, ...marks] })
    const paperId = get().paperId
    if (!paperId) return
    await Promise.all(
      marks.map(async (mark) => {
        try {
          const chunk = await fetchChunk(paperId, mark.chunkId)
          if (get().paperId !== paperId) return
          set((s) => ({
            passages: s.passages.map((p) => (sameMark(p, mark) ? { ...p, boxes: chunk.boxes } : p)),
          }))
        } catch {
          // No boxes: the chip still jumps to the page (§5.8 scanned-PDF fallback).
        }
      }),
    )
  },
  setPassagePinned: (chunkId, messageId, pinned) =>
    set((s) => ({
      passages: s.passages.map((p) => (sameMark(p, { chunkId, messageId }) ? { ...p, pinned } : p)),
    })),
  clearPassages: ({ keepPinned = true } = {}) =>
    set((s) => ({ passages: keepPinned ? s.passages.filter((p) => p.pinned) : [], activeCitation: null })),
  setActiveCitation: (activeCitation) => set({ activeCitation }),

  requestScrollToChunk: async (chunkId, options = {}) => {
    const { paperId } = get()
    if (!paperId) return null
    const jump = nextSeq()
    latestJump = jump
    const from: PaperPosition = { page: get().currentPage, offset: get().currentOffset }
    set({ pendingChunkId: chunkId })
    let chunk: ChunkDetail
    try {
      chunk = await fetchChunk(paperId, chunkId)
    } catch (e) {
      if (latestJump === jump) set({ pendingChunkId: null })
      throw e
    }
    if (latestJump !== jump || get().paperId !== paperId) return null

    const messageId = options.messageId ?? null
    const existing = get().passages.find((p) => p.chunkId === chunkId && (messageId === null || p.messageId === messageId))
    const label = options.label ?? existing?.label ?? ''
    const passages = existing
      ? get().passages.map((p) => (p === existing ? { ...p, boxes: chunk.boxes } : p))
      : [
          ...get().passages,
          {
            chunkId,
            label,
            numeral: 0,
            boxes: chunk.boxes,
            pageStart: chunk.page_start,
            pageEnd: chunk.page_end,
            sectionNumber: chunk.section_number ?? null,
            messageId,
            pinned: false,
          },
        ]
    const boxes = chunk.boxes.length
      ? chunk.boxes
      : // No geometry (a scan): fall back to the top of the first page.
        undefined
    set((s) => ({
      passages,
      pendingChunkId: null,
      activeCitation: { chunkId, label, messageId },
      jumpHistory: [...s.jumpHistory, from].slice(-JUMP_HISTORY_MAX),
      scrollRequest: {
        seq: nextSeq(),
        reason: 'citation',
        behaviour: options.behaviour ?? 'auto',
        chunkId,
        focusBracket: options.viaKeyboard ?? false,
        ...(boxes ? { boxes } : { position: { page: chunk.page_start, offset: 0 } }),
      },
    }))
    return chunk
  },
  requestScrollToPosition: (position, reason = 'page', behaviour = 'auto') =>
    set({ scrollRequest: { seq: nextSeq(), reason, behaviour, position } }),
  jumpBack: () => {
    const history = get().jumpHistory
    const last = history[history.length - 1]
    if (!last) return false
    set({
      jumpHistory: history.slice(0, -1),
      scrollRequest: { seq: nextSeq(), reason: 'back', behaviour: 'auto', position: last },
    })
    return true
  },
  ackScroll: (done) => {
    if (get().scrollRequest?.seq === done) set({ scrollRequest: null })
  },

  requestAsk: (text, quote = null) => set({ askRequest: { seq: nextSeq(), text, quote }, panelOpen: true }),
  consumeAsk: (done) => {
    if (get().askRequest?.seq === done) set({ askRequest: null })
  },
  requestComposerFocus: () => set((s) => ({ composerFocusSeq: s.composerFocusSeq + 1, panelOpen: true })),

  setPanelOpen: (panelOpen) => set({ panelOpen }),
  togglePanel: () => set((s) => ({ panelOpen: !s.panelOpen })),
  setPanelWidth: (width) => {
    const panelWidth = clampPanelWidth(width)
    storage.set(PANEL_WIDTH_KEY, String(panelWidth))
    set({ panelWidth })
  },
  setOutlineOpen: (outlineOpen) => set({ outlineOpen }),
  toggleOutline: () => set((s) => ({ outlineOpen: !s.outlineOpen })),
}))

// ---------------------------------------------------------------- selectors

/** The section a 0-based page starts in: the last heading on or before that page. */
export function sectionAtPage(meta: PaperMeta | null, page: number): PaperMeta['sections'][number] | null {
  if (!meta) return null
  let found: PaperMeta['sections'][number] | null = null
  for (const s of meta.sections) {
    if (s.page <= page) found = s
    else break
  }
  return found
}

/** Passages on one page (for the viewer's per-page bracket layer). */
export const passagesOnPage = (passages: readonly PassageMark[], page: number): PassageMark[] =>
  passages.filter((p) => (p.boxes.length ? p.boxes.some((b) => b.page === page) : p.pageStart <= page && page <= p.pageEnd))
