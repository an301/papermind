/**
 * Session.messages → turns, so a reload shows the whole conversation (§5.18
 * "Returning to a paper with history"). The server persists the question
 * before it streams and the answer (or how it ended) afterwards, each with
 * its sources, gaps and trail.
 *
 * Re-answers are versions of one question (§5.19): the client re-sends the
 * same question and quote, so consecutive questions that match are folded
 * into one turn. "Simpler still" / "More technical still" travel as a last
 * line on the question (see `versionMessage`).
 */
import type { ChatMessage, ErrorCode, Level } from '../../../api/types'
import type { ComposerQuote } from '../../shared/readerStore'
import { newAnswer } from './chatReducer'
import type { Answer, AnswerVariant, ToolStep, Turn } from './types'

/** The instruction appended when Simpler / More technical is pressed at an end of the depth range. */
export const STILL_SUFFIX = { simpler: 'Simpler still, please.', more_technical: 'More technical still, please.' } as const

/** The message to send for a version of `question`. */
export function versionMessage(question: string, variant: AnswerVariant, still: boolean): string {
  if (!still || (variant !== 'simpler' && variant !== 'more_technical')) return question
  return `${question}\n\n${STILL_SUFFIX[variant]}`
}

/** Undo `versionMessage`: the question as the reader wrote it, and the "still" instruction if any. */
export function parseVersionMessage(text: string): { question: string; still: 'simpler' | 'more_technical' | null } {
  for (const kind of ['simpler', 'more_technical'] as const) {
    const suffix = `\n\n${STILL_SUFFIX[kind]}`
    if (text.endsWith(suffix)) return { question: text.slice(0, -suffix.length), still: kind }
  }
  return { question: text, still: null }
}

const ERROR_CODES: ReadonlySet<string> = new Set<ErrorCode>([
  'no_api_key',
  'auth_failed',
  'rate_limited',
  'overloaded',
  'api_error',
  'refusal',
  'paper_not_ready',
  'session_not_found',
  'tool_failed',
  'internal',
])

const LEVEL_RANK: Record<Level, number> = { beginner: 0, intermediate: 1, expert: 2 }

const sameQuote = (a: ComposerQuote | null, b: { text: string; page?: number | null } | null | undefined): boolean =>
  (a?.text ?? null) === (b?.text ?? null) && (a?.page ?? null) === (b?.page ?? null)

function stepsFrom(message: ChatMessage): ToolStep[] {
  const seen: Record<string, number> = {}
  return message.activity.map((item, i) => {
    const failures = seen[item.tool] ?? 0
    seen[item.tool] = item.ok ? 0 : failures + 1
    return {
      callId: `r${i}`,
      tool: item.tool,
      label: item.label,
      summary: item.summary ?? null,
      status: item.ok ? 'done' : 'failed',
      durationMs: item.duration_ms ?? null,
      attempt: failures + 1,
      detail: null,
      startedAt: null,
    }
  })
}

function answerFrom(message: ChatMessage, level: Level, variant: AnswerVariant, still: boolean): Answer {
  const at = Date.parse(message.created_at) || 0
  const base = newAnswer({ level: message.level ?? level, variant, still, at, key: `m:${message.message_id}` })
  const code = message.error_code ?? null
  let phase: Answer['phase'] = 'finished'
  let problem: Answer['problem'] = null
  if (code === 'interrupted') phase = 'stopped'
  else if (code) {
    phase = 'failed'
    problem = {
      kind: 'error',
      code: ERROR_CODES.has(code) ? (code as ErrorCode) : 'internal',
      message: message.error ?? '',
      retryable: true,
    }
  }
  const cited = [...new Set([...message.text.matchAll(/\[([SP]\d+)\]/g)].map((m) => m[1]!))]
  return {
    ...base,
    messageId: message.message_id,
    phase,
    problem,
    steps: stepsFrom(message),
    gaps: message.gaps ?? null,
    sources: message.sources,
    text: message.text,
    cited,
    external: message.external,
    followups: message.followups,
    latencyMs: message.latency_ms ?? null,
    textAt: message.text ? at : null,
    endedAt: at,
    restored: true,
  }
}

/** A question whose answer never got persisted: it was cut off before the server could save anything. */
function orphanAnswer(level: Level, variant: AnswerVariant, still: boolean, at: number, key: string): Answer {
  return { ...newAnswer({ level, variant, still, at, key }), phase: 'dropped', endedAt: at, restored: true }
}

function variantFor(previous: Answer | undefined, level: Level, still: 'simpler' | 'more_technical' | null): AnswerVariant {
  if (still) return still
  if (!previous) return 'original'
  const delta = LEVEL_RANK[level] - LEVEL_RANK[previous.level]
  return delta < 0 ? 'simpler' : delta > 0 ? 'more_technical' : 'retry'
}

export function turnsFromMessages(messages: readonly ChatMessage[], fallbackLevel: Level = 'intermediate'): Turn[] {
  const turns: Turn[] = []
  let pending: { turn: Turn; level: Level; variant: AnswerVariant; still: boolean; message: ChatMessage } | null = null

  const settlePending = () => {
    if (!pending) return
    const { turn, level, variant, still, message } = pending
    const at = Date.parse(message.created_at) || 0
    turn.answers.push(orphanAnswer(level, variant, still, at, `u:${message.message_id}`))
    turn.active = turn.answers.length - 1
    pending = null
  }

  for (const message of messages) {
    if (message.role === 'user') {
      settlePending()
      const { question, still } = parseVersionMessage(message.text)
      const level = message.level ?? fallbackLevel
      const last = turns[turns.length - 1]
      const isVersion = !!last && last.question === question && sameQuote(last.quote, message.quote)
      let turn: Turn
      if (isVersion) turn = last!
      else {
        turn = {
          key: `t:${message.message_id}`,
          question,
          quote: message.quote ? { text: message.quote.text, page: message.quote.page ?? null } : null,
          answers: [],
          active: 0,
        }
        turns.push(turn)
      }
      const previous = turn.answers[turn.answers.length - 1]
      pending = { turn, level, variant: isVersion ? variantFor(previous, level, still) : 'original', still: !!still, message }
      continue
    }
    // An assistant message answers the pending question; one with no question before it starts its own turn.
    if (pending) {
      const { turn, level, variant, still } = pending
      const answer = answerFrom(message, level, variant, still)
      answer.userMessageId = pending.message.message_id
      turn.answers.push(answer)
      turn.active = turn.answers.length - 1
      pending = null
    } else {
      turns.push({
        key: `t:${message.message_id}`,
        question: '',
        quote: null,
        answers: [answerFrom(message, message.level ?? fallbackLevel, 'original', false)],
        active: 0,
      })
    }
  }
  settlePending()
  return turns
}
