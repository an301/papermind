/**
 * How a paper is named and described in the Library (DESIGN.md §5.3, §5.4).
 * Pure functions, shared by the Library rows, the ingestion watcher's toasts
 * and announcements, and the Reader placeholder.
 */
import type { IngestStage, PaperSummary, StageDetail } from '../../api/types'
import { authorsShort, NBSP, pagesLabel, shortDate, stripPdfExtension, yearOf } from '../../lib/format'

export const isReady = (p: Pick<PaperSummary, 'status'>): boolean => p.status === 'ready'
export const isFailed = (p: Pick<PaperSummary, 'status'>): boolean => p.status === 'failed'
export const isWorking = (p: Pick<PaperSummary, 'status'>): boolean => p.status === 'queued' || p.status === 'processing'

/** arXiv id as the reader would type it, from a paper_id ("hep-th_9901001" → "hep-th/9901001"). */
export const arxivIdOf = (paperId: string): string => paperId.replace('_', '/')

/**
 * Until a paper is ready the server only knows what was asked for: "arXiv:1706.03762v5"
 * or the uploaded filename. The Library shows that as a placeholder in --ink-2.
 */
export function hasRealTitle(p: PaperSummary): boolean {
  if (isReady(p)) return true
  if (p.source === 'arxiv') return !/^arxiv:/i.test(p.title)
  return false
}

/** The title to show: the real one, or "arXiv 1706.03762" / the filename without .pdf. */
export function displayTitle(p: PaperSummary): string {
  if (hasRealTitle(p)) return p.title
  if (p.source === 'arxiv') return `arXiv${NBSP}${arxivIdOf(p.paper_id)}`
  return stripPdfExtension(p.title)
}

/**
 * "Vaswani et al., 2017 · 15 pages". Uploads with no metadata: the filename,
 * then "Uploaded Oct 3". Nothing at all while an arXiv paper is being looked up.
 */
export function metaLine(p: PaperSummary): string {
  const who = authorsShort(p.authors, yearOf(p.published))
  const parts: string[] = []
  if (who) parts.push(who)
  else if (p.source === 'upload') parts.push(`Uploaded ${shortDate(p.added_at)}`)
  if (isReady(p) && p.num_pages > 0) parts.push(pagesLabel(p.num_pages))
  return parts.join(' · ')
}

// ---------------------------------------------------------------- ingestion steps

/** The steps shown on an ingestion row, in order (§5.4). `mapping` appears only if the server reports it. */
const STEPS: IngestStage[] = ['downloading', 'extracting', 'sectioning', 'chunking', 'mapping', 'indexing']

/**
 * Where each stage sits on PaperSummary.progress, mirroring _STAGE_SPAN in
 * papermind/api/ingest_jobs.py. Used only to turn overall progress into
 * "Indexing passages… 66%" when the server sends no counts.
 */
const INDEXING_SPAN: [number, number] = [0.6, 0.99]

/**
 * Counts seen earlier in an ingestion, kept so a finished step can still say
 * "Read 25 pages" after the server has moved on to the next stage.
 */
const seenDetail = new Map<string, Partial<Record<IngestStage, StageDetail>>>()

export function rememberStageDetail(p: PaperSummary): void {
  if (!p.stage || !p.stage_detail) return
  const entry = seenDetail.get(p.paper_id) ?? {}
  entry[p.stage] = p.stage_detail
  seenDetail.set(p.paper_id, entry)
}

const detailFor = (p: PaperSummary, stage: IngestStage): StageDetail | undefined =>
  p.stage === stage && p.stage_detail ? p.stage_detail : seenDetail.get(p.paper_id)?.[stage]

function pageCount(p: PaperSummary): number | null {
  if (p.num_pages > 0) return p.num_pages
  const d = detailFor(p, 'extracting')
  return d && d.total > 0 ? d.total : null
}

/** What a finished step says, with its count when one is known. */
export function doneLabel(p: PaperSummary, stage: IngestStage): string {
  switch (stage) {
    case 'downloading':
      return p.source === 'upload' ? 'Uploaded' : 'Downloaded'
    case 'extracting': {
      const n = pageCount(p)
      return n ? `Read ${pagesLabel(n)}` : 'Read the pages'
    }
    case 'sectioning': {
      const d = detailFor(p, 'sectioning')
      return d?.total ? `Found ${d.total}${NBSP}${d.total === 1 ? 'section' : 'sections'}` : 'Found the sections'
    }
    case 'chunking': {
      // Indexing counts the same passages, so its total names the chunking result too.
      const d = detailFor(p, 'chunking') ?? detailFor(p, 'indexing')
      return d?.total ? `${d.total}${NBSP}${d.total === 1 ? 'passage' : 'passages'}` : 'Split into passages'
    }
    case 'mapping': {
      const d = detailFor(p, 'mapping')
      return d?.total ? `Builds on ${d.total}${NBSP}ideas` : 'Found the ideas it builds on'
    }
    case 'indexing':
      return 'Indexed'
    case 'done':
      return 'Ready'
  }
}

/** What the running step says, ending with "…". */
export function runningLabel(p: PaperSummary): string {
  if (p.status === 'queued' || !p.stage) return 'Waiting to start…'
  switch (p.stage) {
    case 'downloading':
      return p.source === 'upload' ? 'Receiving the PDF…' : 'Downloading…'
    case 'extracting': {
      const n = pageCount(p)
      return n ? `Reading ${pagesLabel(n)}…` : 'Reading the pages…'
    }
    case 'sectioning':
      return 'Finding sections…'
    case 'chunking':
      return 'Splitting into passages…'
    case 'mapping':
      return 'Finding the ideas it builds on…'
    case 'indexing': {
      const d = p.stage_detail
      if (d && d.total > 0) return `Indexing ${d.done} of ${d.total}${NBSP}passages…`
      const [from, to] = INDEXING_SPAN
      const pct = Math.round(Math.min(1, Math.max(0, (p.progress - from) / (to - from))) * 100)
      return `Indexing passages… ${pct}%`
    }
    case 'done':
      return 'Ready'
  }
}

export interface IngestSteps {
  /** Finished steps, in order, as their done labels. */
  done: string[]
  /** The running step, or null when finished. */
  current: string | null
  /** For the narrow layout: "3 of 6 steps done". */
  total: number
}

/**
 * Only completed steps and the current one appear, so the line grows as work
 * happens (§5.4). Pending steps are never listed.
 */
export function ingestSteps(p: PaperSummary): IngestSteps {
  const hasMapping = p.stage === 'mapping' || !!seenDetail.get(p.paper_id)?.mapping
  const steps = STEPS.filter((s) => s !== 'mapping' || hasMapping)
  if (p.status === 'queued' || !p.stage) return { done: [], current: runningLabel(p), total: steps.length }
  if (p.stage === 'done') return { done: steps.map((s) => doneLabel(p, s)), current: null, total: steps.length }
  const at = steps.indexOf(p.stage)
  return {
    done: steps.slice(0, Math.max(0, at)).map((s) => doneLabel(p, s)),
    current: runningLabel(p),
    total: steps.length,
  }
}

/** Reading can start as soon as the PDF is on the server (§5.4 "Read early"). */
export function canOpenEarly(p: PaperSummary): boolean {
  if (isReady(p)) return true
  if (!isWorking(p) || !p.stage) return false
  return p.stage !== 'downloading'
}

/** A one-line sentence for the live region when a stage finishes: "Attention Is All You Need: found 9 sections". */
export function stageAnnouncement(p: PaperSummary, finished: IngestStage): string {
  const label = doneLabel(p, finished)
  return `${displayTitle(p)}: ${label.charAt(0).toLowerCase()}${label.slice(1)}`
}

/**
 * The failure sentence for a row. The server's message is already written for
 * the reader (papermind/api/ingest_jobs.py); fall back by code when it's empty.
 */
export function failureText(p: PaperSummary): string {
  if (p.error) return p.error
  switch (p.error_code) {
    case 'arxiv_unreachable':
      return 'Couldn’t reach arXiv. Check your connection and try again, or upload the PDF instead.'
    case 'not_found':
      return `arXiv has no paper ${arxivIdOf(p.paper_id)}. Check the ID, or upload the PDF instead.`
    case 'encrypted':
      return 'Couldn’t finish preparing this paper: the PDF is password-protected.'
    case 'no_text':
      return 'Couldn’t finish preparing this paper: the PDF has no text to search.'
    default:
      return 'Couldn’t finish preparing this paper: indexing failed.'
  }
}

/** Whether "Try again" can help: an arXiv paper whose failure wasn't about the ID itself. */
export function canRetry(p: PaperSummary): boolean {
  if (p.source !== 'arxiv') return false
  return !['invalid_input', 'not_found', 'withdrawn', 'newer_version', 'encrypted', 'no_text', 'not_a_pdf', 'too_large'].includes(
    p.error_code ?? '',
  )
}
