/**
 * The conversation as a pure reducer over ChatEvents (papermind/schemas/events.py):
 *
 *   turn.started → (tool.started | tool.retry | tool.finished | gaps)* → sources
 *   → answer.delta* → answer.done → knowledge.updated? → followups? → usage → done
 *
 * with `error` possible anywhere after turn.started (then `done`). The stream
 * runner (./conversation.ts) adds the transport outcomes the server can't
 * send: the reader stopped it, the connection dropped, the POST failed.
 */
import type { ChatEvent, Level } from '../../../api/types'
import type { ComposerQuote } from '../../shared/readerStore'
import type { Answer, AnswerVariant, ChatState, QueueReason, ToolStep, Turn } from './types'

export type ChatAction =
  | { type: 'reset'; turns?: Turn[] }
  /** A new question, with its first answer. */
  | { type: 'asked'; turn: Turn }
  /** A new version of an existing question (Simpler, More technical, Try again); it becomes the visible one. */
  | { type: 'version'; turnKey: string; answer: Answer }
  /** Replace an answer that never reached the server (Not sent → Try again). */
  | { type: 'resend'; turnKey: string; answerKey: string; startedAt: number }
  | { type: 'event'; turnKey: string; answerKey: string; event: ChatEvent; at: number }
  | { type: 'queued'; turnKey: string; answerKey: string; reason: QueueReason }
  | { type: 'sending'; turnKey: string; answerKey: string; at: number }
  /** The reader pressed Stop (or Esc). */
  | { type: 'stopped'; turnKey: string; answerKey: string; at: number }
  /** The stream ended without `done`. */
  | { type: 'ended'; turnKey: string; answerKey: string; at: number }
  /** The POST failed before streaming. */
  | { type: 'not_sent'; turnKey: string; answerKey: string; at: number; offline?: boolean; reason?: string }
  /** A queued question was cancelled: drop the answer (and the turn when it was the only one). */
  | { type: 'cancelled'; turnKey: string; answerKey: string }
  | { type: 'show_version'; turnKey: string; index: number }

export const initialChatState: ChatState = { turns: [] }

let keySeq = 0
export const newKey = (prefix: string): string => `${prefix}${Date.now().toString(36)}${(++keySeq).toString(36)}`

export function newAnswer(init: {
  level: Level
  variant?: AnswerVariant
  still?: boolean
  at: number
  key?: string
}): Answer {
  return {
    key: init.key ?? newKey('a'),
    messageId: null,
    userMessageId: null,
    level: init.level,
    variant: init.variant ?? 'original',
    still: init.still ?? false,
    phase: 'sending',
    queueReason: null,
    steps: [],
    gaps: null,
    sources: [],
    text: '',
    cited: [],
    external: [],
    knowledge: null,
    followups: [],
    usage: null,
    latencyMs: null,
    problem: null,
    startedAt: init.at,
    textAt: null,
    endedAt: null,
    restored: false,
    failures: {},
  }
}

export function newTurn(question: string, quote: ComposerQuote | null, answer: Answer, key = newKey('t')): Turn {
  return { key, question, quote, answers: [answer], active: 0 }
}

// ---------------------------------------------------------------- one answer

/** Does this gaps event have anything to show (a prerequisite, not just the concept itself)? */
const hasRealGaps = (g: { gaps: { depth: number }[] } | null): boolean => !!g?.gaps.some((x) => x.depth > 0)

/** Apply one server event to an answer. Unknown or out-of-order events leave it as it is. */
export function applyEvent(answer: Answer, event: ChatEvent, at: number): Answer {
  // A finished answer ignores stragglers (a late frame after Stop, say).
  if (answer.phase === 'stopped' || answer.phase === 'dropped' || answer.phase === 'not_sent') return answer
  switch (event.type) {
    case 'turn.started':
      return {
        ...answer,
        phase: answer.phase === 'sending' || answer.phase === 'queued' ? 'working' : answer.phase,
        queueReason: null,
        userMessageId: event.user_message_id,
        level: event.level,
      }

    case 'tool.started': {
      if (answer.steps.some((s) => s.callId === event.call_id)) return answer
      const failures = answer.failures[event.tool] ?? 0
      const step: ToolStep = {
        callId: event.call_id,
        tool: event.tool,
        label: event.label,
        summary: null,
        status: 'running',
        durationMs: null,
        attempt: failures + 1,
        detail: null,
        startedAt: at,
      }
      return { ...answer, phase: workingPhase(answer), steps: [...answer.steps, step] }
    }

    case 'tool.retry':
      // The model's arguments didn't validate; it gets the error and calls again.
      // Nothing ran, so there is no step to show, only the next call's "(second try)".
      return {
        ...answer,
        phase: workingPhase(answer),
        failures: { ...answer.failures, [event.tool]: Math.max(event.attempt, (answer.failures[event.tool] ?? 0) + 1) },
      }

    case 'tool.finished': {
      const exists = answer.steps.some((s) => s.callId === event.call_id)
      const finished = (s: ToolStep): ToolStep => ({
        ...s,
        status: event.ok ? 'done' : 'failed',
        summary: event.summary,
        durationMs: event.duration_ms,
        detail: event.detail ?? null,
      })
      const steps = exists
        ? answer.steps.map((s) => (s.callId === event.call_id ? finished(s) : s))
        : [
            ...answer.steps,
            finished({
              callId: event.call_id,
              tool: event.tool,
              label: event.summary,
              summary: null,
              status: 'running',
              durationMs: null,
              attempt: (answer.failures[event.tool] ?? 0) + 1,
              detail: null,
              startedAt: null,
            }),
          ]
      const failures = { ...answer.failures }
      if (event.ok) delete failures[event.tool]
      else failures[event.tool] = (failures[event.tool] ?? 0) + 1
      return { ...answer, phase: workingPhase(answer), steps, failures }
    }

    case 'gaps':
      // One callout per answer (§5.16): keep the first event that names a real
      // prerequisite; until then the latest one.
      if (hasRealGaps(answer.gaps)) return answer
      return { ...answer, gaps: event }

    case 'sources':
      return { ...answer, phase: workingPhase(answer), sources: event.sources }

    case 'answer.delta':
      return {
        ...answer,
        phase: answer.phase === 'answered' || answer.phase === 'failed' ? answer.phase : 'writing',
        text: answer.text + event.text,
        textAt: answer.textAt ?? at,
      }

    case 'answer.done':
      return {
        ...answer,
        phase: answer.phase === 'failed' ? 'failed' : 'answered',
        messageId: event.message_id,
        text: event.text,
        cited: event.cited,
        external: event.external,
        sources: event.sources.length ? event.sources : answer.sources,
        textAt: answer.textAt ?? at,
        // The trail closes now; compose_answer's own finish may still be on its way.
        steps: answer.steps.map((s) => (s.tool === 'compose_answer' && s.status === 'running' ? { ...s, status: 'done' } : s)),
      }

    case 'knowledge.updated':
      if (!event.added.length && !event.removed.length && !event.ask.length) return answer
      return { ...answer, knowledge: event }

    case 'followups':
      return { ...answer, followups: event.questions.slice(0, 2) }

    case 'usage':
      return { ...answer, usage: event, latencyMs: event.latency_ms }

    case 'error':
      return {
        ...answer,
        phase: 'failed',
        queueReason: null,
        problem: { kind: 'error', code: event.code, message: event.message, retryable: event.retryable },
        steps: closeRunning(answer.steps),
        endedAt: at,
      }

    case 'done': {
      if (answer.phase === 'answered') return { ...answer, phase: 'finished', endedAt: at }
      if (answer.phase === 'failed' || answer.phase === 'finished') return { ...answer, endedAt: answer.endedAt ?? at }
      // `done` with neither an answer nor an error: the server ended the turn
      // without producing anything. Say so rather than wait forever.
      return {
        ...answer,
        phase: 'failed',
        problem: { kind: 'error', code: 'internal', message: '', retryable: true },
        steps: closeRunning(answer.steps),
        endedAt: at,
      }
    }

    default:
      return answer
  }
}

function workingPhase(answer: Answer): Answer['phase'] {
  if (answer.phase === 'sending' || answer.phase === 'queued') return 'working'
  return answer.phase
}

/** Steps still running when a turn ends didn't finish; show them as such rather than spinning forever. */
const closeRunning = (steps: ToolStep[]): ToolStep[] =>
  steps.map((s) => (s.status === 'running' ? { ...s, status: s.tool === 'compose_answer' ? 'done' : 'failed' } : s))

/** The reader stopped the answer: keep everything shown so far. */
export function stopAnswer(answer: Answer, at: number): Answer {
  if (answer.phase === 'answered') return { ...answer, phase: 'finished', endedAt: at }
  if (!['queued', 'sending', 'working', 'writing'].includes(answer.phase)) return answer
  return { ...answer, phase: 'stopped', queueReason: null, steps: closeRunning(answer.steps), endedAt: at }
}

/** The stream closed. After answer.done that's only lost bookkeeping; before it, a dropped connection. */
export function endAnswer(answer: Answer, at: number): Answer {
  if (answer.phase === 'answered') return { ...answer, phase: 'finished', endedAt: at }
  if (answer.phase === 'working' || answer.phase === 'writing' || answer.phase === 'sending') {
    return { ...answer, phase: 'dropped', steps: closeRunning(answer.steps), endedAt: at }
  }
  return answer
}

// ---------------------------------------------------------------- the conversation

function updateAnswer(state: ChatState, turnKey: string, answerKey: string, fn: (a: Answer) => Answer): ChatState {
  let changed = false
  const turns = state.turns.map((t) => {
    if (t.key !== turnKey) return t
    const answers = t.answers.map((a) => {
      if (a.key !== answerKey) return a
      const next = fn(a)
      if (next !== a) changed = true
      return next
    })
    return changed ? { ...t, answers } : t
  })
  return changed ? { turns } : state
}

export function chatReducer(state: ChatState, action: ChatAction): ChatState {
  switch (action.type) {
    case 'reset':
      return { turns: action.turns ?? [] }

    case 'asked':
      return { turns: [...state.turns, action.turn] }

    case 'version':
      return {
        turns: state.turns.map((t) =>
          t.key === action.turnKey ? { ...t, answers: [...t.answers, action.answer], active: t.answers.length } : t,
        ),
      }

    case 'resend':
      return updateAnswer(state, action.turnKey, action.answerKey, (a) => ({
        ...newAnswer({ level: a.level, variant: a.variant, still: a.still, at: action.startedAt, key: a.key }),
      }))

    case 'event':
      return updateAnswer(state, action.turnKey, action.answerKey, (a) => applyEvent(a, action.event, action.at))

    case 'queued':
      return updateAnswer(state, action.turnKey, action.answerKey, (a) =>
        a.phase === 'queued' && a.queueReason === action.reason ? a : { ...a, phase: 'queued', queueReason: action.reason, problem: null },
      )

    case 'sending':
      return updateAnswer(state, action.turnKey, action.answerKey, (a) =>
        a.phase === 'queued' ? { ...a, phase: 'sending', queueReason: null, startedAt: action.at } : a,
      )

    case 'stopped':
      return updateAnswer(state, action.turnKey, action.answerKey, (a) => stopAnswer(a, action.at))

    case 'ended':
      return updateAnswer(state, action.turnKey, action.answerKey, (a) => endAnswer(a, action.at))

    case 'not_sent':
      return updateAnswer(state, action.turnKey, action.answerKey, (a) => ({
        ...a,
        phase: 'not_sent',
        queueReason: null,
        problem: { kind: 'not_sent', offline: action.offline, reason: action.reason },
        endedAt: action.at,
      }))

    case 'cancelled': {
      const turns: Turn[] = []
      for (const t of state.turns) {
        if (t.key !== action.turnKey) {
          turns.push(t)
          continue
        }
        const answers = t.answers.filter((a) => a.key !== action.answerKey)
        if (answers.length) turns.push({ ...t, answers, active: Math.min(t.active, answers.length - 1) })
      }
      return { turns }
    }

    case 'show_version':
      return {
        turns: state.turns.map((t) =>
          t.key === action.turnKey ? { ...t, active: Math.max(0, Math.min(t.answers.length - 1, action.index)) } : t,
        ),
      }

    default:
      return state
  }
}

/** Run a list of events through one answer (tests, and replaying a recorded stream). */
export function replay(answer: Answer, events: readonly ChatEvent[], clock: () => number = () => 0): Answer {
  return events.reduce((a, e) => applyEvent(a, e, clock()), answer)
}
