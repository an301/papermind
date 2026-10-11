import { NBSP, plural } from '../../lib/format'
import type { ReadingProgress } from '../../lib/readingProgress'
import s from './PageStrip.module.css'

/** One slot per page at a 4px pitch (2px mark, 2px gap); at most 40 slots (DESIGN.md §5.3). */
const MAX_SLOTS = 40
const PITCH = 4
const HEIGHT = 12

type SlotState = 'pinned' | 'marks' | 'read' | 'unread'
const RANK: Record<SlotState, number> = { unread: 0, read: 1, marks: 2, pinned: 3 }

export interface StripSlot {
  state: SlotState
  /** First page in this slot is the first appendix page: leave a two-slot gap before it. */
  appendixBefore: boolean
}

/**
 * Bin pages into at most 40 slots. Each slot shows the strongest state in its
 * bin (pinned > marks > read > not yet); a bin counts as read when at least
 * half its pages are read.
 */
export function stripSlots(numPages: number, progress: Pick<ReadingProgress, 'pagesRead' | 'highlightsByPage' | 'pinsByPage' | 'appendixStart'>): StripSlot[] {
  if (numPages <= 0) return []
  const per = Math.ceil(numPages / MAX_SLOTS)
  const read = new Set(progress.pagesRead)
  const slots: StripSlot[] = []
  for (let first = 0; first < numPages; first += per) {
    const pages = Array.from({ length: Math.min(per, numPages - first) }, (_, i) => first + i)
    const readCount = pages.filter((p) => read.has(p)).length
    let state: SlotState = readCount * 2 >= pages.length && readCount > 0 ? 'read' : 'unread'
    for (const p of pages) {
      if ((progress.pinsByPage[p] ?? 0) > 0) state = 'pinned'
      else if ((progress.highlightsByPage[p] ?? 0) > 0 && RANK[state] < RANK.marks) state = 'marks'
    }
    const appendix = progress.appendixStart ?? null
    slots.push({ state, appendixBefore: appendix !== null && appendix > 0 && pages.includes(appendix) })
  }
  return slots
}

export function stripLabel(numPages: number, progress: ReadingProgress): string {
  const readTo = progress.pagesRead.length ? Math.max(...progress.pagesRead) + 1 : 0
  const highlights = Object.values(progress.highlightsByPage).reduce((a, b) => a + b, 0)
  const pins = Object.values(progress.pinsByPage).reduce((a, b) => a + b, 0)
  const parts = [readTo ? `Read to p.${NBSP}${readTo} of ${numPages}` : `Not read yet, ${numPages} pages`]
  if (highlights) parts.push(plural(highlights, 'highlight'))
  if (pins) parts.push(plural(pins, 'pinned answer'))
  return parts.join(', ')
}

/** The reading strip: where you've worked in this paper, not how much is left. */
export function PageStrip({ numPages, progress }: { numPages: number; progress: ReadingProgress }) {
  const slots = stripSlots(numPages, progress)
  // x position of each slot: a 4px pitch, plus a two-slot gap before the appendix.
  const xs: number[] = []
  let cursor = 0
  for (const slot of slots) {
    if (slot.appendixBefore) cursor += PITCH * 2
    xs.push(cursor)
    cursor += PITCH
  }
  const marks = slots.map((slot, i) => {
    const at = xs[i] ?? 0
    switch (slot.state) {
      case 'pinned':
        return <rect key={i} x={at} y={0} width={2} height={HEIGHT} className={s.pinned} />
      case 'marks':
        // A read bar with a 2px notch cut 3px from the top.
        return (
          <g key={i} className={s.read}>
            <rect x={at} y={0} width={2} height={3} />
            <rect x={at} y={5} width={2} height={7} />
          </g>
        )
      case 'read':
        return <rect key={i} x={at} y={0} width={2} height={HEIGHT} className={s.read} />
      default:
        return <rect key={i} x={at} y={HEIGHT - 2} width={2} height={2} className={s.unread} />
    }
  })
  const width = Math.max(0, cursor - 2)
  const label = stripLabel(numPages, progress)
  return (
    <svg role="img" aria-label={label} className={s.strip} width={width} height={HEIGHT} viewBox={`0 0 ${width} ${HEIGHT}`}>
      <title>{label}</title>
      {marks}
    </svg>
  )
}
