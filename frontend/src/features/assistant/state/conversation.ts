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
import { ApiError, api, isAbort, isNotFound } from '../../../api/client'
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

/** In-flight streams by answer key. */
const controllers = new Map<string, AbortController>()
/** Answers waiting for the paper to finish indexing. */
const waiting = new Map<string, () => void>()

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

    const session = useReaderStore.getState().session
    if (!session) {
      dispatch({ type: 'not_sent', turnKey, answerKey, at: Date.now() })
      return
    }

    const controller = new AbortController()
    controllers.set(answerKey, controller)
    let sawDone = false
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
      if (!sawDone) dispatch({ type: 'ended', turnKey, answerKey, at: Date.now() })
    } catch (e) {
      if (isAbort(e) || controller.signal.aborted) return // stop() already recorded it
      const answer = findAnswer(turnKey, answerKey)
      const started = answer && answer.phase !== 'sending'
      if (started) dispatch({ type: 'ended', turnKey, answerKey, at: Date.now() })
      else dispatch({ type: 'not_sent', turnKey, answerKey, at: Date.now(), offline: !(e instanceof ApiError) && !navigator.onLine })
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
    chat: initialChatState,

    open: async (paperId, userId, level) => {
      if (get().paperId === paperId && get().sessionStatus !== 'failed') return
      for (const c of controllers.values()) c.abort()
      controllers.clear()
      set({ paperId, sessionStatus: 'loading', sessionRenewed: false, chat: initialChatState })
      try {
        const { session, renewed } = await restoreOrCreate(paperId, userId, level)
        if (get().paperId !== paperId) return
        const turns = turnsFromMessages(session.messages, session.level)
        useReaderStore.getState().setSession(session)
        set({ sessionStatus: 'ready', sessionRenewed: renewed, chat: { turns } })
        showLatestSources(turns)
      } catch {
        if (get().paperId === paperId) set({ sessionStatus: 'failed' })
      }
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
      void run(key, answer.key, message, quote, depth)
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
      void run(turnKey, answerKey, versionMessage(turn.question, answer.variant, answer.still), turn.quote, answer.level)
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
      set({ paperId: null })
      await get().open(paperId, userId, level)
    },

    busy: () => get().chat.turns.some((t) => t.answers.some((a) => STOPPABLE_PHASES.has(a.phase))),
  }
})

/** Is any answer in this conversation still live (streaming or waiting)? */
export const anyLive = (state: ChatState): boolean => state.turns.some((t) => t.answers.some(isLive))
