/**
 * The assistant's view of a conversation. One `Turn` is a question (and the
 * passage it quoted); it holds one or more `Answer`s, because Simpler, More
 * technical and Try again re-answer the same question as a new version
 * (DESIGN.md §5.19). Wire types stay in src/api/types.ts; these are derived
 * from the ChatEvent stream (live) or from Session.messages (restored).
 */
import type { ErrorCode, ExternalRef, GapsFound, KnowledgeUpdated, Level, SourceRef, UsageReport } from '../../../api/types'
import type { ComposerQuote } from '../../shared/readerStore'

export type StepStatus = 'running' | 'done' | 'failed'

/** One tool call in the activity trail (§5.11). */
export interface ToolStep {
  callId: string
  tool: string
  /** Present-tense line from tool.started, e.g. "Searching the paper for ‘…’…". */
  label: string
  /** Past-tense line from tool.finished. */
  summary: string | null
  status: StepStatus
  durationMs: number | null
  /** 1 for a first call, 2 for "(second try)" after a failed or invalid call of the same tool. */
  attempt: number
  detail: Record<string, unknown> | null
  /** Client clock (ms) when tool.started arrived; null for restored steps. */
  startedAt: number | null
}

/**
 * Where an answer is. The live ones are `queued` (waiting for the paper or the
 * network), `sending` (POST in flight), `working` (tools), `writing`
 * (answer.delta) and `answered` (answer.done arrived; knowledge, follow-ups
 * and usage may still come). The rest are final.
 */
export type AnswerPhase =
  | 'queued'
  | 'sending'
  | 'working'
  | 'writing'
  | 'answered'
  | 'finished'
  | 'failed'
  | 'stopped'
  | 'dropped'
  | 'not_sent'

export type QueueReason = 'paper' | 'offline'

/** Why an answer has no complete text, as the turn shows it (§5.25). */
export type AnswerProblem =
  | { kind: 'error'; code: ErrorCode; message: string; retryable: boolean }
  /** The request failed before streaming (HTTP 4xx/5xx or no connection): "Not sent." */
  | { kind: 'not_sent'; offline?: boolean }

/** How a version came about, for the pager ("Version 2 of 2 · Simpler"). */
export type AnswerVariant = 'original' | 'simpler' | 'more_technical' | 'retry'

export interface Answer {
  /** Client key, stable for React and for the stream that fills it. */
  key: string
  /** The persisted assistant message id (answer.done / restored); null until then. */
  messageId: string | null
  userMessageId: string | null
  level: Level
  variant: AnswerVariant
  /** "Simpler still" / "More technical still" at an end of the depth range. */
  still: boolean
  phase: AnswerPhase
  queueReason: QueueReason | null
  steps: ToolStep[]
  gaps: GapsFound | null
  sources: SourceRef[]
  /** Markdown with [S1]/[P1] markers: the deltas so far, then answer.done's cleaned text. */
  text: string
  /** Labels the final text cites (answer.done.cited). */
  cited: string[]
  external: ExternalRef[]
  knowledge: KnowledgeUpdated | null
  followups: string[]
  usage: UsageReport | null
  /** Total turn time (usage.latency_ms, or the persisted message's). */
  latencyMs: number | null
  problem: AnswerProblem | null
  /** Client clock (ms): request sent, first answer token, end of stream. */
  startedAt: number
  textAt: number | null
  endedAt: number | null
  /** Came from Session.messages, not a live stream. */
  restored: boolean
  /** A tool.retry arrived for this tool and its next call is a retry: tool → failures so far. */
  failures: Record<string, number>
}

export interface Turn {
  key: string
  question: string
  quote: ComposerQuote | null
  answers: Answer[]
  /** Index of the version on screen. */
  active: number
}

export interface ChatState {
  turns: Turn[]
}

export const LIVE_PHASES: ReadonlySet<AnswerPhase> = new Set(['queued', 'sending', 'working', 'writing', 'answered'])
/** The composer shows Stop for these: the reader is waiting on the answer text. */
export const STOPPABLE_PHASES: ReadonlySet<AnswerPhase> = new Set(['queued', 'sending', 'working', 'writing'])

export const isLive = (a: Pick<Answer, 'phase'>): boolean => LIVE_PHASES.has(a.phase)
export const activeAnswer = (t: Turn): Answer => t.answers[t.active] ?? t.answers[t.answers.length - 1]!
