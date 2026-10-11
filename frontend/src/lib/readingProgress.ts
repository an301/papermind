/**
 * Where the reader has been in each paper, kept per device in localStorage
 * under `pm:read:{paperId}`. The Reader writes it; the Library reads it for
 * the right column ("You stopped at §3.2"), the page strip, and the sort
 * order (last opened first). See DESIGN.md §5.3.
 *
 * "Read" means at least 50% of the page's height was in the viewport for a
 * cumulative 8 s; the Reader decides that and calls `markPagesRead`.
 * Highlights and pins live here too until the server stores marks (Appendix A12).
 */
import { useSyncExternalStore } from 'react'
import { storage } from './storage'

export interface ReadingProgress {
  paperId: string
  /** Last time the paper was opened in the Reader (ISO). */
  openedAt: string
  /** 0-based page the reader was last on. */
  lastPage: number
  /** Number of the section the reader was last in, e.g. '3.2'; null when the paper has no numbered sections. */
  lastSection: string | null
  /** 0-based indexes of pages that count as read. */
  pagesRead: number[]
  /** Highlight count per 0-based page. */
  highlightsByPage: Record<number, number>
  /** Pinned-answer count per 0-based page. */
  pinsByPage: Record<number, number>
  /** 0-based index of the first appendix page (a section numbered 'A', 'A.1' …), if any. */
  appendixStart?: number | null
}

const PREFIX = 'pm:read:'
const key = (paperId: string) => `${PREFIX}${paperId}`

const listeners = new Set<() => void>()
let version = 0
function emit() {
  version++
  listeners.forEach((l) => l())
}

export function getProgress(paperId: string): ReadingProgress | null {
  return storage.getJson<ReadingProgress | null>(key(paperId), null)
}

function update(paperId: string, fn: (p: ReadingProgress) => ReadingProgress): void {
  const current = getProgress(paperId) ?? {
    paperId,
    openedAt: new Date().toISOString(),
    lastPage: 0,
    lastSection: null,
    pagesRead: [],
    highlightsByPage: {},
    pinsByPage: {},
  }
  storage.setJson(key(paperId), fn(current))
  emit()
}

/** The Reader opened this paper (moves it to the top of the Library). */
export const recordOpen = (paperId: string): void =>
  update(paperId, (p) => ({ ...p, openedAt: new Date().toISOString() }))

/** Where the reader is now. Call on scroll settle, not per frame. */
export const recordPosition = (paperId: string, pos: { page: number; section: string | null }): void =>
  update(paperId, (p) => ({ ...p, lastPage: pos.page, lastSection: pos.section }))

export const markPagesRead = (paperId: string, pages: number[]): void =>
  update(paperId, (p) => ({ ...p, pagesRead: [...new Set([...p.pagesRead, ...pages])].sort((a, b) => a - b) }))

export const setMarks = (
  paperId: string,
  marks: Partial<Pick<ReadingProgress, 'highlightsByPage' | 'pinsByPage' | 'appendixStart'>>,
): void => update(paperId, (p) => ({ ...p, ...marks }))

export function forgetProgress(paperId: string): void {
  storage.remove(key(paperId))
  emit()
}

function subscribe(cb: () => void): () => void {
  listeners.add(cb)
  const onStorage = (e: StorageEvent) => {
    if (e.key?.startsWith(PREFIX)) emit()
  }
  window.addEventListener('storage', onStorage)
  return () => {
    listeners.delete(cb)
    window.removeEventListener('storage', onStorage)
  }
}

/** Re-renders when any paper's progress changes; read individual records with getProgress. */
export function useProgressVersion(): number {
  return useSyncExternalStore(subscribe, () => version, () => 0)
}
