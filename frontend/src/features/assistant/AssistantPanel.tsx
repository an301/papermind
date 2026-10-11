/**
 * The assistant panel: the paper's outer margin (DESIGN.md §4.4, §5.9–5.20).
 * Header with the Depth control, the thread, the composer. It creates or
 * restores the paper's session itself and answers ask requests from the
 * Reader (Explain from the selection popover, "Explain softmax first").
 */
import { ArrowDownIcon, ClockIcon, XIcon } from '@phosphor-icons/react'
import { useEffect, useRef, useState } from 'react'
import { useSuggestions, useUpdateSession, useUser } from '../../api/queries'
import type { Level } from '../../api/types'
import { Button } from '../../components/Button'
import { ErrorState } from '../../components/ErrorState'
import { IconButton } from '../../components/IconButton'
import { SegmentedControl } from '../../components/SegmentedControl'
import { Skeleton } from '../../components/Skeleton'
import { announce } from '../../lib/announce'
import { clsx } from '../../lib/clsx'
import { useDelayedFlag } from '../../lib/useDelayedFlag'
import { prefersReducedMotion } from '../../lib/motion'
import { storage } from '../../lib/storage'
import { LEVEL_OPTIONS, Onboarding, useNeedsOnboarding } from '../onboarding'
import { sectionAtPage, useReaderStore } from '../shared/readerStore'
import { Composer } from './Composer'
import { useConversation } from './state/conversation'
import { activeAnswer, STOPPABLE_PHASES } from './state/types'
import { TurnView } from './turn/TurnView'
import s from './AssistantPanel.module.css'
import t from './turn/Turn.module.css'

const hiddenKey = (paperId: string) => `pm:suggestions-hidden:${paperId}`
const DEPTH_OPTIONS = LEVEL_OPTIONS.map((o) => ({ value: o.value, label: o.label, tooltip: o.tooltip }))

export interface AssistantPanelProps {
  /** The phone sheet's smallest detent: latest answer's first line and the composer. */
  mode?: 'full' | 'peek'
}

function Suggestions({ paperId, level, onAsk }: { paperId: string; level: Level; onAsk: (q: string) => void }) {
  const [hidden, setHidden] = useState(() => storage.get(hiddenKey(paperId)) === '1')
  const suggestions = useSuggestions(paperId, level, { enabled: !hidden })
  const showSkeleton = useDelayedFlag(suggestions.isLoading)
  if (hidden || suggestions.isError) return null
  if (suggestions.isLoading) {
    return showSkeleton ? (
      <div className={s.skeletons} aria-hidden="true">
        <Skeleton width="80%" height={14} />
        <Skeleton width="65%" height={14} />
        <Skeleton width="72%" height={14} />
      </div>
    ) : null
  }
  const questions = suggestions.data?.questions.slice(0, 3) ?? []
  if (!questions.length) return null
  return (
    <div className={s.suggestionsBlock}>
      <ul className={t.suggestions} aria-label="Suggested questions" role="list">
        {questions.map((q) => (
          <li key={q}>
            <button type="button" className={t.suggestion} onClick={() => onAsk(q)}>
              {q}
            </button>
          </li>
        ))}
      </ul>
      <IconButton
        label="Hide suggestions"
        icon={XIcon}
        size="xs"
        className={s.hideSuggestions}
        onClick={() => {
          storage.set(hiddenKey(paperId), '1')
          setHidden(true)
        }}
      />
    </div>
  )
}

export function AssistantPanel({ mode = 'full' }: AssistantPanelProps) {
  const paperId = useReaderStore((st) => st.paperId)
  const meta = useReaderStore((st) => st.meta)
  const level = useReaderStore((st) => st.level)
  const session = useReaderStore((st) => st.session)
  const askRequest = useReaderStore((st) => st.askRequest)
  const currentPage = useReaderStore((st) => st.currentPage)
  const user = useUser()
  const userId = user.data?.user_id
  const needsOnboarding = useNeedsOnboarding()

  const turns = useConversation((st) => st.chat.turns)
  const status = useConversation((st) => st.sessionStatus)
  const renewed = useConversation((st) => st.sessionRenewed)
  const open = useConversation((st) => st.open)
  const ask = useConversation((st) => st.ask)
  const stop = useConversation((st) => st.stop)
  const updateSession = useUpdateSession()

  const threadRef = useRef<HTMLDivElement>(null)
  const [atEnd, setAtEnd] = useState(true)
  const [restore, setRestore] = useState<{ seq: number; text: string } | null>(null)
  const askedThisVisit = useRef(false)

  const streaming = turns.some((turn) => turn.answers.some((a) => STOPPABLE_PHASES.has(a.phase)))

  useEffect(() => {
    if (paperId && userId) void open(paperId, userId, level)
    // Open once per paper and user; a depth change applies to the next question only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [paperId, userId, open])

  // Announce once when an answer finishes (§5.13 ARIA).
  const lastFinished = useRef<string | null>(null)
  useEffect(() => {
    const last = turns[turns.length - 1]
    if (!last) return
    const answer = activeAnswer(last)
    if (answer.phase === 'finished' && !answer.restored && lastFinished.current !== answer.key) {
      lastFinished.current = answer.key
      const n = answer.cited.filter((l) => l.startsWith('S')).length
      announce(n ? `Answer ready, ${n} ${n === 1 ? 'citation' : 'citations'}.` : 'Answer ready.')
    }
  }, [turns])

  const send = (question: string, quote = useReaderStore.getState().quote) => {
    const key = ask({ question, quote })
    if (!key) return false
    askedThisVisit.current = true
    useReaderStore.getState().setQuote(null)
    // On send, the new question's top sits 24px under the header; the panel then does not follow the answer.
    requestAnimationFrame(() => {
      const el = threadRef.current?.querySelector<HTMLElement>(`[data-turn="${CSS.escape(key)}"]`)
      const thread = threadRef.current
      if (el && thread) thread.scrollTo({ top: el.offsetTop - 24, behavior: prefersReducedMotion() ? 'auto' : 'smooth' })
    })
    return true
  }

  // Ask requests from the Reader (Explain, "Explain softmax first", Recap…).
  useEffect(() => {
    if (!askRequest || status !== 'ready') return
    useReaderStore.getState().consumeAsk(askRequest.seq)
    send(askRequest.text, askRequest.quote)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [askRequest, status])

  // Restored conversation: scroll to the last answer.
  useEffect(() => {
    const thread = threadRef.current
    if (status === 'ready' && turns.length && thread && !askedThisVisit.current) {
      // The last turn's question at the top, not the bottom spacer (§5.18 "Returning").
      const last = thread.querySelector<HTMLElement>(`[data-turn="${CSS.escape(turns[turns.length - 1]!.key)}"]`)
      thread.scrollTop = last ? last.offsetTop - 24 : thread.scrollHeight
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status])

  const onScroll = () => {
    const el = threadRef.current
    if (el) setAtEnd(el.scrollHeight - el.scrollTop - el.clientHeight < 80)
  }

  const changeLevel = (next: Level) => {
    useReaderStore.getState().setLevel(next)
    if (session) updateSession.mutate({ sessionId: session.session_id, level: next })
  }

  if (!paperId) return null

  if (mode === 'peek') {
    const last = turns[turns.length - 1]
    const firstLine = last ? activeAnswer(last).text.split('\n').find((l) => l.trim()) : null
    return (
      <div className={s.peek}>
        {firstLine ? <p className={s.peekLine}>{firstLine.replace(/\[[SP]\d+\]/g, '')}</p> : null}
        <Composer paperId={paperId} streaming={streaming} onSend={(q) => send(q)} onStop={stop} restore={restore} />
      </div>
    )
  }

  const section = sectionAtPage(meta, currentPage)
  const empty = !turns.length

  return (
    <div className={s.panel}>
      <header className={s.header}>
        <span id="depth-label" className={s.depthLabel}>
          Depth
        </span>
        <SegmentedControl<Level>
          value={level}
          onValueChange={(v) => changeLevel(v)}
          options={DEPTH_OPTIONS}
          segmentWidth={84}
          aria-labelledby="depth-label"
        />
        <select
          className={s.depthSelect}
          aria-label="Depth"
          value={level}
          onChange={(e) => changeLevel(e.target.value as Level)}
        >
          {LEVEL_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </header>

      <div className={s.thread} ref={threadRef} onScroll={onScroll}>
        {!meta ? (
          <p className={clsx(s.status, s.intro)}>
            <ClockIcon size={14} aria-hidden="true" /> Still indexing this paper. You can read now; questions will send when it’s ready.
          </p>
        ) : null}
        {renewed ? <p className={clsx(s.status, s.intro)}>This conversation expired after a day without activity, so a new one was started.</p> : null}

        {status === 'failed' ? (
          <div className={s.intro}>
            <ErrorState
              action={
                <Button size="sm" onClick={() => paperId && userId && void open(paperId, userId, level)}>
                  Try again
                </Button>
              }
            >
              Can’t reach the PaperMind server. If you’re running it yourself, start it with make dev.
            </ErrorState>
          </div>
        ) : empty && status === 'ready' ? (
          needsOnboarding ? (
            <div className={s.intro}>
              <Onboarding onDone={() => useReaderStore.getState().requestComposerFocus()} />
            </div>
          ) : (
            <div className={s.intro}>
              <p className={s.introText}>
                Ask about anything on these pages, or select a sentence and press E. If an answer assumes too much, press Simpler.
              </p>
              {meta ? <Suggestions paperId={paperId} level={level} onAsk={(q) => send(q, null)} /> : null}
            </div>
          )
        ) : null}

        {turns.map((turn, i) => (
          <TurnView
            key={turn.key}
            turn={turn}
            latest={i === turns.length - 1}
            calloutsAllowed={!needsOnboarding}
            onRephrase={(q) => setRestore({ seq: Date.now(), text: q })}
          />
        ))}

        {!empty && !askedThisVisit.current && section && status === 'ready' && !streaming ? (
          <p className={clsx(s.status, s.returning)}>
            You stopped at {section.number ? `§${section.number}` : section.title} last time.{' '}
            <button
              type="button"
              className={t.inlineAction}
              onClick={() => send(`Recap what I’ve read so far, up to ${section.number ? `§${section.number}` : section.title}.`, null)}
            >
              Recap up to {section.number ? `§${section.number}` : section.title}
            </button>
          </p>
        ) : null}
        <div className={s.spacer} aria-hidden="true" />
      </div>

      {!atEnd && !empty ? (
        <button
          type="button"
          className={s.jump}
          onClick={() => threadRef.current?.scrollTo({ top: threadRef.current.scrollHeight, behavior: prefersReducedMotion() ? 'auto' : 'smooth' })}
        >
          <ArrowDownIcon size={14} aria-hidden="true" /> Jump to latest
        </button>
      ) : null}

      <Composer paperId={paperId} streaming={streaming} onSend={(q) => send(q)} onStop={stop} restore={restore} />
    </div>
  )
}
