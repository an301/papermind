/**
 * How a KNOWS edge reads in "What you know" (DESIGN.md §5.22). No
 * percentages, ever: a state word and one honest evidence sentence whose verb
 * matches the kind of signal behind it.
 */
import type { ConceptArea, EvidenceItem, KnownConcept } from '../../api/types'
import { shortDate, smarten } from '../../lib/format'

export type ConceptState = 'Comfortable' | 'Seen it'

/**
 * Comfortable: the reader said so (directly or in onboarding), or the
 * inference is confident. Seen it: an inference below that, including edges
 * under the server's known threshold (0.6), which the agent doesn't yet treat
 * as known. "Not yet" rows need Appendix A9 (`include=not_yet`).
 */
export const COMFORTABLE_FROM = 0.7

export function conceptState(k: Pick<KnownConcept, 'source' | 'confidence'>): ConceptState {
  if (k.source === 'self_reported' || k.source === 'onboarding') return 'Comfortable'
  return k.confidence >= COMFORTABLE_FROM ? 'Comfortable' : 'Seen it'
}

/** One evidence line, split so the reader's own words can be set in italic and the locator can be a link. */
export interface EvidenceLine {
  /** Text before the quote, e.g. "You asked". */
  lead: string
  /** The reader's own words, shown in curly quotes and Literata italic. */
  quote?: string
  /** Text after the quote (rare). */
  tail?: string
  /** Where it happened: paper title and section, when known (Appendix A9). */
  paper?: { paperId: string; label: string; section?: string | null }
  /** "Oct 3". */
  date: string
}

const ONBOARDING_RE = /^Onboarding: comfortable with (.+?)\.?$/i
const SELF_REPORTED_DEFAULT = /^the reader said they know this\.?$/i

function trimQuote(text: string): string {
  const t = smarten(text.trim().replace(/^["“”']+|["“”']+$/g, ''))
  return t.length > 140 ? `${t.slice(0, 139).trimEnd()}…` : t
}

function fromItem(item: EvidenceItem, areaName: (id: string) => string): EvidenceLine {
  const date = shortDate(item.at)
  const paper = item.paper_id && item.paper_title ? { paperId: item.paper_id, label: item.paper_title, section: item.section } : undefined
  switch (item.kind) {
    case 'onboarding':
      return { lead: `You said you’re comfortable with ${areaName(item.section ?? '') || 'this area'}`, date }
    case 'said':
      return { lead: 'You said you know this', date }
    case 'explained':
      return item.quote
        ? { lead: 'You explained it', quote: trimQuote(item.quote), paper, date }
        : { lead: 'You explained it', paper, date }
    case 'came_up':
      return { lead: 'Came up while reading', paper, date }
    case 'asked':
    default:
      return item.quote ? { lead: 'You asked', quote: trimQuote(item.quote), paper, date } : { lead: 'You asked about it', paper, date }
  }
}

/**
 * The newest evidence first. Uses `evidence_items` when the server sends them
 * (Appendix A9); otherwise one line from `source` + `evidence`:
 * - onboarding: "You said you’re comfortable with Linear algebra · Oct 2"
 * - self-reported: "You said you know this · Oct 3"
 * - inferred: the reader's own words, "You asked “wait, what’s softmax?” · Oct 3"
 *   ("You wrote …" when it wasn't a question; a question is never "You explained").
 */
export function evidenceLines(k: KnownConcept, areas: readonly ConceptArea[] = []): EvidenceLine[] {
  const areaName = (id: string) => areas.find((a) => a.area_id === id)?.name ?? id
  if (k.evidence_items?.length) {
    return [...k.evidence_items].sort((a, b) => Date.parse(b.at) - Date.parse(a.at)).map((i) => fromItem(i, areaName))
  }
  const date = shortDate(k.updated_at)
  const text = k.evidence.trim()
  if (k.source === 'onboarding') {
    const m = ONBOARDING_RE.exec(text)
    const area = m?.[1] ?? (k.area ? areaName(k.area) : '')
    return [{ lead: area ? `You said you’re comfortable with ${area}` : 'You said you’re comfortable with this area', date }]
  }
  if (k.source === 'self_reported') {
    if (!text || SELF_REPORTED_DEFAULT.test(text)) return [{ lead: 'You said you know this', date }]
    return [{ lead: 'You said', quote: trimQuote(text), date }]
  }
  if (!text) return [{ lead: 'From your questions', date }]
  const quote = trimQuote(text)
  return [{ lead: quote.endsWith('?') ? 'You asked' : 'You wrote', quote, date }]
}

/** Plain-text form, for accessible descriptions and tests. */
export function evidenceText(line: EvidenceLine): string {
  const quote = line.quote ? ` “${line.quote}”` : ''
  const where = line.paper ? `, in ${line.paper.label}${line.paper.section ? `, §${line.paper.section}` : ''}` : ''
  return `${line.lead}${quote}${line.tail ?? ''}${where} · ${line.date}`
}
