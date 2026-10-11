/**
 * The agent activity trail's words (DESIGN.md §5.11): one live line while the
 * answer is being worked out, then one summary sentence with the steps behind
 * a disclosure. The live lines come from the server's tool labels; the
 * summary is assembled here from counts so the UI never parses a sentence.
 */
import { listJoin, NBSP, smarten } from '../../../lib/format'
import type { Answer, ToolStep } from './types'

/** The writer's hand-off. It has no live line of its own: the trail moves to "Writing the answer…". */
export const WRITER_TOOL = 'compose_answer'
const SCHOLAR_TOOLS = new Set(['lookup_paper', 'get_references', 'get_citations'])

export const FIRST_LINE = 'Finding the relevant parts of the paper…'
export const WRITING_LINE = 'Writing the answer…'
export const SCHOLAR_SLOW = 'Semantic Scholar is slow right now.'

/** "3.1 s" with a no-break space, one decimal. */
export const seconds = (ms: number): string => `${(Math.round(ms / 100) / 10).toFixed(1)}${NBSP}s`

/** Unique section references of the sources in order: "§3.2.1", "§3.2.2" (or the title when unnumbered). */
export function sourceSections(sources: Answer['sources']): string[] {
  const seen: string[] = []
  for (const s of sources) {
    const ref = s.section_number ? `§${s.section_number}` : s.section_title
    if (ref && !seen.includes(ref)) seen.push(ref)
  }
  return seen
}

/**
 * The live line, or null while nothing should show yet (the first 300 ms
 * before any tool starts, §5.11).
 */
export function liveLine(answer: Answer, elapsedMs: number): string | null {
  if (answer.phase === 'writing' || answer.text) return WRITING_LINE
  if (answer.sources.length) return `Reading from ${listJoin(sourceSections(answer.sources).slice(0, 4))}…`
  const steps = answer.steps.filter((s) => s.tool !== WRITER_TOOL)
  const running = [...steps].reverse().find((s) => s.status === 'running')
  const latest = running ?? steps[steps.length - 1]
  if (latest) return smarten(latest.label)
  return elapsedMs >= 300 ? FIRST_LINE : null
}

/** After 20 s, the reason when one is known: a Semantic Scholar call still running. */
export function slowReason(answer: Answer, elapsedMs: number): string | null {
  if (elapsedMs < 20_000) return null
  return answer.steps.some((s) => s.status === 'running' && SCHOLAR_TOOLS.has(s.tool)) ? SCHOLAR_SLOW : null
}

const count = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`

function matchedConcept(step: ToolStep): boolean {
  if (step.status !== 'done') return false
  if (step.detail && 'matched' in step.detail) return step.detail.matched != null
  // Restored steps carry no detail; the summary says when nothing matched.
  return !/isn’t in the concept map|isn't in the concept map/.test(step.summary ?? '')
}

function scholarCount(steps: ToolStep[]): number {
  const ids = new Set<string>()
  let withoutDetail = 0
  for (const s of steps) {
    if (s.status !== 'done') continue
    const found = s.detail?.s2_ids
    if (Array.isArray(found)) {
      // A lookup returns candidates; the paper looked up is the first.
      if (found[0] != null) ids.add(String(found[0]))
    } else if (!/^Nothing on Semantic Scholar/.test(s.summary ?? '')) withoutDetail++
  }
  return ids.size + withoutDetail
}

function listCount(steps: ToolStep[]): number {
  let n = 0
  for (const s of steps) {
    if (s.status !== 'done') continue
    const papers = s.detail?.s2_ids
    if (Array.isArray(papers)) n += papers.length
    else n += Number(/Found (\d+)/.exec(s.summary ?? '')?.[1] ?? 0)
  }
  return n
}

/**
 * "Read 4 passages and checked 2 ideas, 3.1 s" — clauses in the order the
 * tools first ran, then the total turn time. With no tools: "Answered from
 * the conversation, 1.2 s".
 */
export function summaryLine(answer: Answer, fallbackMs: number | null = null): string {
  const steps = answer.steps.filter((s) => s.tool !== WRITER_TOOL)
  const order: string[] = []
  for (const s of steps) if (!order.includes(s.tool)) order.push(s.tool)
  const clauses: string[] = []
  for (const tool of order) {
    const mine = steps.filter((s) => s.tool === tool)
    switch (tool) {
      case 'search_paper':
        clauses.push(answer.sources.length ? `read ${count(answer.sources.length, 'passage', 'passages')}` : 'searched the paper')
        break
      case 'check_prerequisites': {
        const n = mine.filter(matchedConcept).length
        if (n) clauses.push(`checked ${count(n, 'idea', 'ideas')}`)
        break
      }
      case 'get_learning_path':
        if (mine.some((s) => s.status === 'done')) clauses.push('worked out a learning path')
        break
      case 'lookup_paper': {
        const n = scholarCount(mine)
        if (n) clauses.push(`looked up ${count(n, 'paper', 'papers')}`)
        break
      }
      case 'get_references': {
        const n = listCount(mine)
        if (n) clauses.push(`found ${count(n, 'reference', 'references')}`)
        break
      }
      case 'get_citations': {
        const n = listCount(mine)
        if (n) clauses.push(`found ${count(n, 'citing paper', 'citing papers')}`)
        break
      }
      default:
        break
    }
  }
  const ms = answer.latencyMs ?? fallbackMs
  const time = ms != null ? `, ${seconds(ms)}` : ''
  if (!clauses.length) return `Answered from the conversation${time}`
  const sentence = listJoin(clauses)
  return `${sentence[0]!.toUpperCase()}${sentence.slice(1)}${time}`
}

const TRY_WORDS = ['', '', 'second', 'third', 'fourth', 'fifth']

/** One row of the expanded trail: the past-tense line, "(second try)" for a retry. */
export function stepLine(step: ToolStep): string {
  const text = smarten(step.summary ?? step.label)
  if (step.attempt <= 1) return text
  const nth = TRY_WORDS[step.attempt]
  return nth ? `${text} (${nth} try)` : `${text} (try ${step.attempt})`
}

/** "Couldn’t reach Semantic Scholar, so this answer uses the paper alone." when a lookup failed but an answer came. */
export function partialFailure(answer: Answer): string | null {
  if (!answer.text) return null
  const scholarFailed = answer.steps.some((s) => s.status === 'failed' && SCHOLAR_TOOLS.has(s.tool))
  const scholarWorked = answer.steps.some((s) => s.status === 'done' && SCHOLAR_TOOLS.has(s.tool))
  return scholarFailed && !scholarWorked ? 'Couldn’t reach Semantic Scholar, so this answer uses the paper alone.' : null
}

/** The steps to list when the trail is expanded: finished ones in order, the writer last. */
export function expandedSteps(answer: Answer): ToolStep[] {
  const tools = answer.steps.filter((s) => s.tool !== WRITER_TOOL && s.status !== 'running')
  const writer = answer.steps.filter((s) => s.tool === WRITER_TOOL && s.status !== 'running')
  return [...tools, ...writer]
}

/** Did any research tool run (so an uncited answer is "general background", §5.13)? */
export const toolsRan = (answer: Answer): boolean => answer.steps.some((s) => s.tool !== WRITER_TOOL)
