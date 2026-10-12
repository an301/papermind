/**
 * The stream runner: one conversation per open paper, kept outside React so
 * the panel can unmount (collapsed, sheet closed) while an answer streams.
 *
 * It owns the session (create or restore, §5.18), sends questions through
 * `streamChat` and folds every ChatEvent into ./chatReducer. It also adds the
 * outcomes the server can't send: the reader stopped, the connection dropped,
 * the POST never got through, the paper wasn't ready yet.
 */
import { create } from 'zustand'
import { ApiError, NetworkError, api, isAbort, isApiError, isNotFound } from '../../../api/client'
import { queryClient } from '../../../api/queryClient'
import { streamChat } from '../../../api/sse'
import type { ChatEvent, Level, Session } from '../../../api/types'
import { storage } from '../../../lib/storage'
import { useReaderStore, type ComposerQuote } from '../../shared/readerStore'
import { chatReducer, initialChatState, newAnswer, newTurn, type ChatAction } from './chatReducer'
import { turnsFromMessages, versionMessage } from './transcript'
import type { AnswerVariant, ChatState, Turn } from './types'
import { activeAnswer, isLive, STOPPABLE_PHASES } from './types'

const sessionKey = (paperId: string) => `pm:session:${paperId}`

export type SessionStatus = 'idle' | 'loading' | 'ready' | 'failed'

interface ConversationState {
  paperId: string | null
  sessionStatus: SessionStatus
  /** "Started a new conversation" after Redis expired the old one (24 h). */
  sessionRenewed: boolean
  /** Why the session couldn't be opened, for the failed state and for "Not sent." */
  sessionError: string | null
  chat: ChatState
}

interface AskOptions {
  question: string
  quote?: ComposerQuote | null
  level?: Level
  /** Re-answer an existing turn as a new version (§5.19). */
  turnKey?: string
  variant?: AnswerVariant
  still?: boolean
}

interface ConversationActions {
  /** Open (or switch to) a paper's conversation: restore its session or start one. */
  open: (paperId: string, userId: string, level: Level) => Promise<void>
  /** Send a question; returns the turn key. */
  ask: (options: AskOptions) => string | null
  /** Stop the answer that is streaming (Esc / Stop). */
  stop: () => void
  /** Re-send an answer that never reached the server. */
  retryNotSent: (turnKey: string, answerKey: string) => void
  showVersion: (turnKey: string, index: number) => void
  /** Forget this paper's session and start over ("New conversation"). */
  restart: (userId: string, level: Level) => Promise<void>
  /** Is an answer being waited on right now? */
  busy: () => boolean
}

type Store = ConversationState & ConversationActions

/** What a reader is told when a request failed before anything streamed. */
export function failureReason(e: unknown): string {
  if (e instanceof ApiError && e.message) return e.message
  if (e instanceof NetworkError) return 'The PaperMind server didn’t respond.'
  return 'The question couldn’t be sent.'
}

/** In-flight streams by answer key. */
const controllers = new Map<string, AbortController>()
/** Answers waiting (for the paper to finish indexing, or their turn); calling one releases it. */
const waiting = new Map<string, () => void>()
/**
 * Answers run one at a time. The server answers each question with the
 * history before it, so two at once would double the cost and interleave the
 * saved transcript. "Explain X first" or Simpler pressed mid-answer waits here.
 */
let lane: Promise<void> = Promise.resolve()

export const useConversation = create<Store>()((set, get) => {
  const dispatch = (action: ChatAction) => set((s) => ({ chat: chatReducer(s.chat, action) }))

  const findAnswer = (turnKey: string, answerKey: string) =>
    get().chat.turns.find((t) => t.key === turnKey)?.answers.find((a) => a.key === answerKey)

  async function restoreOrCreate(paperId: string, userId: string, level: Level): Promise<{ session: Session; renewed: boolean }> {
    const stored = storage.get(sessionKey(paperId))
    if (stored) {
      try {
        const session = await api<Session>(`/sessions/${encodeURIComponent(stored)}`)
        if (session.paper_id === paperId) return { session, renewed: false }
      } catch (e) {
        if (!isNotFound(e)) throw e
      }
    }
    const session = await api<Session>('/sessions', { method: 'POST', body: { user_id: userId, paper_id: paperId, level } })
    storage.set(sessionKey(paperId), session.session_id)
    return { session, renewed: !!stored }
  }

  /** Who and at what level the current paper's conversation was opened for; reused to reconnect. */
  let opened: { userId: string; level: Level } | null = null
  /** The open/reconnect in progress, so a question asked meanwhile waits for it instead of failing. */
  let connecting: Promise<void> | null = null

  /**
   * Restore or create this paper's session and install it. `keepChat` reconnects
   * under a thread the reader is already looking at (a question is waiting on it)
   * instead of replacing that thread with the restored transcript.
   */
  function connect(paperId: string, userId: string, level: Level, keepChat: boolean): Promise<void> {
    opened = { userId, level }
    set(keepChat ? { sessionStatus: 'loading', sessionError: null } : { paperId, sessionStatus: 'loading', sessionRenewed: false, sessionError: null, chat: initialChatState })
    const attempt = (async () => {
      try {
        const { session, renewed } = await restoreOrCreate(paperId, userId, level)
        if (get().paperId !== paperId) return
        useReaderStore.getState().setSession(session)
        if (keepChat) {
          set({ sessionStatus: 'ready', sessionRenewed: get().sessionRenewed || renewed })
        } else {
          // Questions asked while this was loading are already in the thread
          // (and waiting on this session); they go after the restored history.
          const restored = turnsFromMessages(session.messages, session.level)
          const turns = [...restored, ...get().chat.turns]
          set({ sessionStatus: 'ready', sessionRenewed: renewed, chat: { turns } })
          showLatestSources(restored)
        }
      } catch (e) {
        if (get().paperId === paperId) set({ sessionStatus: 'failed', sessionError: failureReason(e) })
      }
    })()
    connecting = attempt
    void attempt.finally(() => {
      if (connecting === attempt) connecting = null
    })
    return attempt
  }

  /** This paper's session, waiting for an open in progress and retrying one that failed. */
  async function ensureSession(): Promise<Session | null> {
    const paperId = get().paperId
    const current = () => {
      const s = useReaderStore.getState().session
      return s && s.paper_id === paperId ? s : null
    }
    if (current()) return current()
    if (connecting) await connecting
    if (current()) return current()
    // Opening failed, or the reader was closed and reopened and forgot it: connect again.
    if (paperId && opened) await connect(paperId, opened.userId, opened.level, true)
    return current()
  }

  /** The session expired or the server lost it (Redis restarted): start a new one for this paper. */
  async function renewSession(): Promise<Session | null> {
    const paperId = get().paperId
    if (!paperId || !opened) return null
    storage.remove(sessionKey(paperId))
    const level = useReaderStore.getState().level
    const session = await api<Session>('/sessions', { method: 'POST', body: { user_id: opened.userId, paper_id: paperId, level } })
    storage.set(sessionKey(paperId), session.session_id)
    if (get().paperId !== paperId) return null
    useReaderStore.getState().setSession(session)
    set({ sessionRenewed: true, sessionStatus: 'ready', sessionError: null })
    return session
  }

  /** Run an answer after any that are already running; while it waits it shows as queued. */
  function enqueue(turnKey: string, answerKey: string, start: () => Promise<void>) {
    const before = lane
    const mustWait = controllers.size > 0 || get().chat.turns.some((t) => t.answers.some((a) => a.key !== answerKey && STOPPABLE_PHASES.has(a.phase)))
    const job = (async () => {
      if (mustWait) {
        dispatch({ type: 'queued', turnKey, answerKey, reason: 'turn' })
        await Promise.race([before, new Promise<void>((resolve) => waiting.set(answerKey, resolve))])
        waiting.delete(answerKey)
        if (findAnswer(turnKey, answerKey)?.phase !== 'queued') return // stopped while waiting
        dispatch({ type: 'sending', turnKey, answerKey, at: Date.now() })
      } else {
        await before
      }
      await start()
    })()
    lane = job.catch(() => {})
  }

  /** Mirror the latest finished answer's citations onto the page (§1.2: the newest answer is active). */
  function showLatestSources(turns: Turn[]) {
    for (let i = turns.length - 1; i >= 0; i--) {
      const answer = activeAnswer(turns[i]!)
      if (answer.sources.length && answer.text) {
        void useReaderStore.getState().showSources(answer.messageId, answer.sources, { cited: answer.cited, text: answer.text })
        return
      }
    }
  }

  async function run(turnKey: string, answerKey: string, message: string, quote: ComposerQuote | null, level: Level) {
    const reader = useReaderStore.getState()
    // Paper still indexing: hold the question until it's ready (§5.10 "Queued").
    if (!reader.meta) {
      dispatch({ type: 'queued', turnKey, answerKey, reason: 'paper' })
      await new Promise<void>((resolve) => {
        const unsubscribe = useReaderStore.subscribe((s) => {
          if (s.meta) {
            unsubscribe()
            waiting.delete(answerKey)
            resolve()
          }
        })
        waiting.set(answerKey, () => {
          unsubscribe()
          resolve()
        })
      })
      if (findAnswer(turnKey, answerKey)?.phase !== 'queued') return
      dispatch({ type: 'sending', turnKey, answerKey, at: Date.now() })
    }

    // Asked while the session was still opening, or after opening it failed:
    // wait for it (or try once more) rather than refusing the question.
    let session = await ensureSession()
    if (findAnswer(turnKey, answerKey)?.phase !== 'sending') return // stopped while waiting
    if (!session) {
      dispatch({ type: 'not_sent', turnKey, answerKey, at: Date.now(), reason: get().sessionError ?? 'The conversation couldn’t be started.' })
      return
    }

    const controller = new AbortController()
    controllers.set(answerKey, controller)
    let sawDone = false
    try {
      for (let attempt = 0; ; attempt++) {
        try {
          const stream = streamChat(
            { session_id: session.session_id, message, quote: quote ? { text: quote.text, page: quote.page ?? null } : null, level },
            controller.signal,
          )
          for await (const event of stream) {
            dispatch({ type: 'event', turnKey, answerKey, event, at: Date.now() })
            onEvent(event)
            if (event.type === 'done') sawDone = true
          }
          break
        } catch (e) {
          // The session expired (24 h) or the server lost it: start a new one and
          // send the question again, once, instead of refusing it forever.
          if (attempt === 0 && isApiError(e, 'session_not_found') && !controller.signal.aborted) {
            const fresh = await renewSession().catch(() => null)
            if (fresh && !controller.signal.aborted) {
              session = fresh
              continue
            }
          }
          throw e
        }
      }
      if (!sawDone) dispatch({ type: 'ended', turnKey, answerKey, at: Date.now() })
    } catch (e) {
      if (isAbort(e) || controller.signal.aborted) return // stop() already recorded it
      const answer = findAnswer(turnKey, answerKey)
      const started = answer && answer.phase !== 'sending'
      if (started) dispatch({ type: 'ended', turnKey, answerKey, at: Date.now() })
      else {
        const offline = !(e instanceof ApiError) && !navigator.onLine
        dispatch({ type: 'not_sent', turnKey, answerKey, at: Date.now(), offline, reason: offline ? undefined : failureReason(e) })
      }
    } finally {
      controllers.delete(answerKey)
    }
  }

  function onEvent(event: ChatEvent) {
    const reader = useReaderStore.getState()
    switch (event.type) {
      case 'turn.started':
        // A new question clears the previous answer's unpinned brackets (§5.8 Visibility).
        reader.clearPassages({ keepPinned: true })
        break
      case 'sources':
        if (event.sources.length) void reader.showSources(null, event.sources)
        break
      case 'answer.done':
        void reader.showSources(event.message_id, event.sources, { cited: event.cited, text: event.text })
        break
      case 'knowledge.updated':
        void queryClient.invalidateQueries({ queryKey: ['knowledge'] })
        break
      default:
        break
    }
  }

  return {
    paperId: null,
    sessionStatus: 'idle',
    sessionRenewed: false,
    sessionError: null,
    chat: initialChatState,

    open: async (paperId, userId, level) => {
      if (get().paperId === paperId && get().sessionStatus !== 'failed') {
        opened = { userId, level }
        if (connecting) await connecting
        const session = useReaderStore.getState().session
        if (session && session.paper_id === paperId) return
        // Back to a paper opened earlier: closing the reader cleared its session.
        // Reconnect and restore the thread (and its brackets), unless an answer is
        // still streaming into the thread on screen.
        await connect(paperId, userId, level, get().busy())
        return
      }
      // Retrying after a failure keeps any question the reader is waiting on.
      const retrying = get().paperId === paperId
      if (!retrying) {
        for (const c of controllers.values()) c.abort()
        controllers.clear()
      }
      await connect(paperId, userId, level, retrying && get().chat.turns.length > 0)
    },

    ask: ({ question, quote = null, level, turnKey, variant = 'original', still = false }) => {
      const text = question.trim()
      if (!text) return null
      const reader = useReaderStore.getState()
      const depth = level ?? reader.level
      const answer = newAnswer({ level: depth, variant, still, at: Date.now() })
      let key = turnKey
      if (key && get().chat.turns.some((t) => t.key === key)) {
        dispatch({ type: 'version', turnKey: key, answer })
      } else {
        const turn = newTurn(text, quote, answer)
        key = turn.key
        dispatch({ type: 'asked', turn })
      }
      const message = versionMessage(text, variant, still)
      const answerKey = answer.key
      const turnKeyNow = key
      enqueue(turnKeyNow, answerKey, () => run(turnKeyNow, answerKey, message, quote, depth))
      return key
    },

    stop: () => {
      const at = Date.now()
      for (const turn of get().chat.turns) {
        for (const answer of turn.answers) {
          if (!STOPPABLE_PHASES.has(answer.phase)) continue
          controllers.get(answer.key)?.abort()
          waiting.get(answer.key)?.()
          dispatch({ type: 'stopped', turnKey: turn.key, answerKey: answer.key, at })
        }
      }
    },

    retryNotSent: (turnKey, answerKey) => {
      const turn = get().chat.turns.find((t) => t.key === turnKey)
      const answer = turn?.answers.find((a) => a.key === answerKey)
      if (!turn || !answer) return
      dispatch({ type: 'resend', turnKey, answerKey, startedAt: Date.now() })
      enqueue(turnKey, answerKey, () => run(turnKey, answerKey, versionMessage(turn.question, answer.variant, answer.still), turn.quote, answer.level))
    },

    showVersion: (turnKey, index) => {
      dispatch({ type: 'show_version', turnKey, index })
      const turn = get().chat.turns.find((t) => t.key === turnKey)
      if (!turn) return
      const answer = activeAnswer(turn)
      if (answer.sources.length && answer.text) {
        void useReaderStore.getState().showSources(answer.messageId, answer.sources, { cited: answer.cited, text: answer.text })
      }
    },

    restart: async (userId, level) => {
      const paperId = get().paperId
      if (!paperId) return
      get().stop()
      storage.remove(sessionKey(paperId))
      useReaderStore.getState().clearPassages({ keepPinned: false })
      useReaderStore.getState().setSession(null)
      set({ paperId: null })
      await get().open(paperId, userId, level)
    },

    busy: () => get().chat.turns.some((t) => t.answers.some((a) => STOPPABLE_PHASES.has(a.phase))),
  }
})

// "Will send when you're back online": keep that promise.
if (typeof window !== 'undefined') {
  window.addEventListener('online', () => {
    const { chat, retryNotSent } = useConversation.getState()
    for (const turn of chat.turns) {
      for (const answer of turn.answers) {
        if (answer.phase === 'not_sent' && answer.problem?.kind === 'not_sent' && answer.problem.offline) retryNotSent(turn.key, answer.key)
      }
    }
  })
}

/** Is any answer in this conversation still live (streaming or waiting)? */
export const anyLive = (state: ChatState): boolean => state.turns.some((t) => t.answers.some(isLive))
