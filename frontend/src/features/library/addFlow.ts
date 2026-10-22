/**
 * Adding a paper (DESIGN.md §5.4): by arXiv id or URL, or by PDF (field,
 * file picker or a drop anywhere on the Library). Everything the reader needs
 * to know about it goes in the hint line under the field, never in a toast;
 * that line is a small store here so the field, the drop overlay and the
 * first-run sample link all write to the same place.
 */
import { create } from 'zustand'
import { api, ApiError, isApiError, NetworkError } from '../../api/client'
import { qk } from '../../api/keys'
import { upsertPaperInCache } from '../../api/queries'
import { queryClient } from '../../api/queryClient'
import type { PaperSummary } from '../../api/types'
import { announce } from '../../lib/announce'
import { megabytes } from '../../lib/format'
import { bootstrapUser } from '../../lib/user'
import { parseArxiv, type ArxivRef } from '../../lib/arxiv'
import { displayTitle } from './paperText'
import { startUpload } from './uploads'

const configured = Number(import.meta.env.VITE_MAX_UPLOAD_MB)
/** DESIGN.md says 50 MB; deployments can change it with VITE_MAX_UPLOAD_MB (keep it in step with the server's MAX_UPLOAD_MB). */
export const MAX_UPLOAD_MB = Number.isFinite(configured) && configured > 0 ? configured : 50

export type Hint =
  | { kind: 'idle' }
  /** Not an id the client can read. Neutral: nothing was lost. */
  | { kind: 'invalid'; message: string }
  /** A valid id after a paste: Enter adds it. */
  | { kind: 'ready'; ref: ArxivRef }
  | { kind: 'adding'; ref: ArxivRef }
  | { kind: 'exists'; title: string; paperId: string }
  /** The server or the browser refused it. `danger` when a file was rejected. */
  | { kind: 'refused'; message: string; tone: 'danger' | 'neutral'; retry?: () => void; actions?: HintAction[] }

/** A way forward from a refusal: "Add v5", "Add v3", "Open it" (DESIGN.md §5.25). */
export interface HintAction {
  label: string
  run?: () => void
  /** A link instead of a button. */
  to?: string
}

export const useAddHint = create<{ hint: Hint }>()(() => ({ hint: { kind: 'idle' } }))
/**
 * Set the hint line. Problems and "already there" are also read out through the
 * page's live region (§8.1), since the hint itself is only aria-describedby.
 */
export function setHint(hint: Hint): void {
  useAddHint.setState({ hint })
  if (hint.kind === 'invalid' || hint.kind === 'refused') announce(hint.message)
  else if (hint.kind === 'exists') announce(`${hint.title} is already in your papers.`)
}

export const NOT_AN_ID = 'That doesn’t look like an arXiv ID. Try one like 2401.12345, or drop a PDF.'
/** On a touch screen there's no dropping a file on the page; the field's Choose PDF is the way. */
const notAnId = (): string =>
  typeof window !== 'undefined' && window.matchMedia?.('(pointer: coarse)').matches
    ? 'That doesn’t look like an arXiv ID. Try one like 2401.12345, or choose a PDF.'
    : NOT_AN_ID
export const NOT_A_PDF = 'That file isn’t a PDF. PaperMind reads PDFs and arXiv papers.'
const UNREACHABLE = 'Couldn’t reach the PaperMind server. Check that it’s running, then try again.'
const OFFLINE = 'You’re offline. Adding a paper needs a connection.'

/** Validate without adding (paste, blur). Returns the parsed id, or null and shows why. */
export function validateArxiv(raw: string): ArxivRef | null {
  const text = raw.trim()
  if (!text) {
    setHint({ kind: 'idle' })
    return null
  }
  const ref = parseArxiv(text)
  if (!ref) {
    setHint({ kind: 'invalid', message: notAnId() })
    return null
  }
  return ref
}

const cachedPapers = (): PaperSummary[] => queryClient.getQueryData<PaperSummary[]>(qk.papers) ?? []

/**
 * Add by arXiv id. Resolves 'added' (a new row is in the list), 'exists'
 * (it was already there; the hint says so) or 'failed' (the hint says why).
 */
export async function addArxiv(raw: string, options: { keepVersion?: boolean } = {}): Promise<'added' | 'exists' | 'failed'> {
  const ref = validateArxiv(raw)
  if (!ref) return 'failed'
  const known = cachedPapers().find((p) => p.paper_id === ref.paperId)
  if (known && known.status !== 'failed') {
    setHint({ kind: 'exists', title: displayTitle(known), paperId: known.paper_id })
    return 'exists'
  }
  if (typeof navigator !== 'undefined' && navigator.onLine === false) {
    setHint({ kind: 'refused', message: OFFLINE, tone: 'neutral' })
    return 'failed'
  }
  setHint({ kind: 'adding', ref })
  try {
    // A failed row for the same paper has to go before it can be added again.
    if (known?.status === 'failed') await api<void>(`/papers/${encodeURIComponent(known.paper_id)}`, { method: 'DELETE' }).catch(() => {})
    // The paper joins the sender's library (X-Reader-Id), so the id must exist first.
    await bootstrapUser()
    const summary = await api<PaperSummary>('/papers', {
      method: 'POST',
      body: { arxiv: ref.display },
      query: options.keepVersion ? { keep_version: true } : undefined,
    })
    upsertPaperInCache(queryClient, summary)
    void queryClient.invalidateQueries({ queryKey: qk.papers, exact: true })
    if (summary.status === 'ready') {
      setHint({ kind: 'exists', title: displayTitle(summary), paperId: summary.paper_id })
      return 'exists'
    }
    setHint({ kind: 'idle' })
    return 'added'
  } catch (e) {
    if (e instanceof NetworkError) {
      setHint({ kind: 'refused', message: UNREACHABLE, tone: 'neutral', retry: () => void addArxiv(raw) })
    } else if (isApiError(e, 'invalid_input')) {
      setHint({ kind: 'invalid', message: e.message || notAnId() })
    } else if (e instanceof ApiError && ['newer_version', 'withdrawn', 'not_found', 'already_added'].includes(e.code)) {
      // Retrying would only get the same answer; offer what the reader can do instead.
      const detail = (e.detail ?? {}) as { latest_version?: string | null; paper_id?: string | null }
      const actions: HintAction[] = []
      if (e.code === 'already_added' && detail.paper_id) {
        actions.push({ label: 'Open it', to: `/paper/${encodeURIComponent(detail.paper_id)}` })
      } else if (detail.latest_version) {
        actions.push({ label: `Add ${detail.latest_version}`, run: () => void addArxiv(`${ref.arxivId}${detail.latest_version}`) })
      }
      if (e.code === 'newer_version' && ref.version) {
        actions.push({ label: `Add ${ref.version}`, run: () => void addArxiv(raw, { keepVersion: true }) })
      }
      setHint({ kind: 'refused', message: e.message, tone: 'neutral', actions })
    } else if (isApiError(e, 'arxiv_unreachable')) {
      setHint({ kind: 'refused', message: e.message, tone: 'neutral', retry: () => void addArxiv(raw) })
    } else if (e instanceof ApiError) {
      setHint({ kind: 'refused', message: e.message || 'Couldn’t add that paper.', tone: 'neutral', retry: () => void addArxiv(raw) })
    } else {
      setHint({ kind: 'refused', message: 'Couldn’t add that paper.', tone: 'neutral', retry: () => void addArxiv(raw) })
    }
    return 'failed'
  }
}

export const isPdfFile = (f: File): boolean => f.type === 'application/pdf' || /\.pdf$/i.test(f.name)

/** Check and upload dropped or chosen files. Problems go to the hint line; good files start uploading. */
export async function addFiles(files: readonly File[]): Promise<void> {
  if (!files.length) return
  const pdfs = files.filter(isPdfFile)
  if (pdfs.length < files.length) {
    setHint({ kind: 'refused', message: NOT_A_PDF, tone: 'danger' })
    if (!pdfs.length) return
  }
  const tooBig = pdfs.find((f) => f.size > MAX_UPLOAD_MB * 1024 * 1024)
  if (tooBig) {
    setHint({ kind: 'refused', message: `That PDF is ${megabytes(tooBig.size)}. The limit is ${MAX_UPLOAD_MB} MB.`, tone: 'danger' })
  }
  const ok = pdfs.filter((f) => f !== tooBig)
  if (!ok.length) return
  if (typeof navigator !== 'undefined' && navigator.onLine === false) {
    setHint({ kind: 'refused', message: OFFLINE, tone: 'neutral' })
    return
  }
  if (pdfs.length === files.length && !tooBig) setHint({ kind: 'idle' })
  await Promise.all(
    ok.map(async (file) => {
      const outcome = await startUpload(file)
      if (outcome.refused) setHint({ kind: 'refused', message: outcome.refused.message, tone: 'danger' })
      else if (outcome.result?.status === 200) {
        const s = outcome.result.summary
        setHint({ kind: 'exists', title: displayTitle(s), paperId: s.paper_id })
      }
    }),
  )
}
