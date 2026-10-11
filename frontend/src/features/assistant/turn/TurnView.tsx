/**
 * One question and its answer (DESIGN.md §5.10–5.19), flat: no bubble, avatar
 * or name. Order: quote, question, trail, background line, prerequisite note,
 * answer, problem line, knowledge line, version pager, actions, follow-ups.
 */
import { CaretLeftIcon, CaretRightIcon, ClockIcon, WarningCircleIcon, WarningIcon } from '@phosphor-icons/react'
import { memo, useId, useState } from 'react'
import { useAddKnowledge, useForgetKnowledge } from '../../../api/queries'
import type { ConceptRef, ErrorCode, Level } from '../../../api/types'
import { IconButton } from '../../../components/IconButton'
import { copyText } from '../../../lib/clipboard'
import { announce } from '../../../lib/announce'
import { listJoin, NBSP } from '../../../lib/format'
import { useReaderStore } from '../../shared/readerStore'
import { AnswerMarkdown } from '../answer/AnswerMarkdown'
import { useConversation } from '../state/conversation'
import { toolsRan } from '../state/trail'
import type { Answer, Turn } from '../state/types'
import { activeAnswer, isLive } from '../state/types'
import { GapCallout } from './GapCallout'
import { Trail } from './Trail'
import s from './Turn.module.css'

const ERROR_COPY: Record<ErrorCode, string> = {
  rate_limited: 'Lots of questions right now. Try again in a moment.',
  overloaded: 'The model is busy at the moment. Your question is saved.',
  api_error: 'This answer didn’t finish. Nothing you wrote was lost.',
  internal: 'This answer didn’t finish. Nothing you wrote was lost.',
  tool_failed: 'Couldn’t search the paper just now, so there’s no answer rather than a guess.',
  paper_not_ready: 'Still indexing this paper. Your question will send when it’s ready.',
  refusal: 'Try asking about what the paper itself says.',
  no_api_key: 'The server has no Anthropic API key. Set ANTHROPIC_API_KEY on the server and restart it.',
  auth_failed: 'The server’s Anthropic API key was rejected. Check the key, then restart the server.',
  session_not_found: 'This conversation expired after a day without activity. Ask again to start a new one.',
}

const LEVELS: Level[] = ['beginner', 'intermediate', 'expert']
const VARIANT_LABEL = { original: '', simpler: 'Simpler', more_technical: 'More technical', retry: 'Asked again' } as const

/** Markdown with chips turned into "(§3.2.1, p. 4)" for Copy (§5.19). */
function plainCopy(answer: Answer): string {
  return answer.text
    .replace(/\[(S\d+)\]/g, (_, label: string) => {
      const src = answer.sources.find((x) => x.label === label)
      if (!src) return ''
      const page = `p.${NBSP}${src.page_start + 1}`
      return src.section_number ? ` (§${src.section_number}, ${page})` : ` (${page})`
    })
    .replace(/\[(P\d+)\]/g, (_, label: string) => {
      const ref = answer.external.find((e) => e.label === label)
      return ref ? ` (${ref.title}${ref.year ? `, ${ref.year}` : ''})` : ''
    })
    .replace(/ +([.,;:])/g, '$1')
}

function Problem({ answer, onRetry, onRephrase }: { answer: Answer; onRetry: () => void; onRephrase: () => void }) {
  if (answer.phase === 'stopped') {
    return (
      <p className={s.problem}>
        This answer stopped partway. Your question and the text so far are saved.{' '}
        <button type="button" className={s.inlineAction} onClick={onRetry}>
          Try again
        </button>
      </p>
    )
  }
  if (answer.phase === 'dropped') {
    return (
      <p className={s.problem}>
        <WarningCircleIcon size={14} className={s.danger} aria-hidden="true" /> The connection dropped partway through. The text so far
        is kept.{' '}
        <button type="button" className={s.inlineAction} onClick={onRetry}>
          Try again
        </button>
      </p>
    )
  }
  const problem = answer.problem
  if (answer.phase !== 'failed' || problem?.kind !== 'error') return null
  const warn = problem.code === 'no_api_key' || problem.code === 'auth_failed'
  const wait = problem.code === 'rate_limited' || problem.code === 'overloaded' || problem.code === 'paper_not_ready'
  const lost = problem.code === 'api_error' || problem.code === 'internal' || problem.code === 'tool_failed'
  return (
    <p className={s.problem} role="alert">
      {warn ? <WarningIcon size={14} className={s.warn} aria-hidden="true" /> : null}
      {wait ? <ClockIcon size={14} aria-hidden="true" /> : null}
      {lost ? <WarningCircleIcon size={14} className={s.danger} aria-hidden="true" /> : null}{' '}
      {problem.code === 'refusal' && problem.message ? `${problem.message} ` : ''}
      {ERROR_COPY[problem.code]}{' '}
      {problem.code === 'refusal' ? (
        <button type="button" className={s.inlineAction} onClick={onRephrase}>
          Rephrase
        </button>
      ) : warn ? null : (
        <button type="button" className={s.inlineAction} onClick={onRetry}>
          Try again
        </button>
      )}
    </p>
  )
}

function KnowledgeLine({ added, removed }: { added: ConceptRef[]; removed: ConceptRef[] }) {
  const [undone, setUndone] = useState(false)
  const add = useAddKnowledge()
  const forget = useForgetKnowledge()
  if (!added.length && !removed.length) return null
  if (undone) return <p className={s.noteLine}>Undone.</p>
  const undo = async () => {
    await Promise.allSettled([
      ...added.map((c) => forget.mutateAsync(c.concept_id)),
      ...removed.map((c) => add.mutateAsync(c.concept_id)),
    ])
    setUndone(true)
    announce('Undone')
  }
  const text = added.length
    ? `Added ${listJoin(added.map((c) => c.name))} to what you know.`
    : `Took ${listJoin(removed.map((c) => c.name))} off what you know.`
  return (
    <p className={s.noteLine}>
      {text}{' '}
      <button type="button" className={s.inlineAction} onClick={undo}>
        Undo
      </button>
    </p>
  )
}

function Actions({ turn, answer, latest }: { turn: Turn; answer: Answer; latest: boolean }) {
  const [copied, setCopied] = useState(false)
  const ask = useConversation((st) => st.ask)
  const live = isLive(answer) && answer.phase !== 'answered'
  const rank = LEVELS.indexOf(answer.level)

  const reanswer = (direction: -1 | 1) => {
    const next = LEVELS[rank + direction]
    ask({
      question: turn.question,
      quote: turn.quote,
      turnKey: turn.key,
      level: next ?? answer.level,
      variant: direction < 0 ? 'simpler' : 'more_technical',
      still: !next,
    })
  }

  const copy = async () => {
    if (await copyText(plainCopy(answer))) {
      setCopied(true)
      announce('Copied')
      window.setTimeout(() => setCopied(false), 1500)
    }
  }

  return (
    <div className={s.actions} data-latest={latest || undefined} data-hidden={live || !answer.text || undefined}>
      <button type="button" className={s.action} onClick={copy}>
        {copied ? 'Copied' : 'Copy'}
      </button>
      <button type="button" className={s.action} onClick={() => reanswer(-1)}>
        Simpler
      </button>
      <button type="button" className={s.action} onClick={() => reanswer(1)}>
        More technical
      </button>
    </div>
  )
}

export interface TurnViewProps {
  turn: Turn
  latest: boolean
  /** Hide prerequisite notes while first-run onboarding is open (§5.16). */
  calloutsAllowed: boolean
  onRephrase: (question: string) => void
}

export const TurnView = memo(function TurnView({ turn, latest, calloutsAllowed, onRephrase }: TurnViewProps) {
  const headingId = useId()
  const answer = activeAnswer(turn)
  const ask = useConversation((st) => st.ask)
  const retryNotSent = useConversation((st) => st.retryNotSent)
  const showVersion = useConversation((st) => st.showVersion)
  const streaming = answer.phase === 'writing' || answer.phase === 'working' || answer.phase === 'sending'
  const generalOnly = !!answer.text && !answer.cited.some((l) => l.startsWith('S')) && toolsRan(answer) && !isLive(answer)

  const retry = () => {
    if (answer.phase === 'not_sent') retryNotSent(turn.key, answer.key)
    else ask({ question: turn.question, quote: turn.quote, turnKey: turn.key, level: answer.level, variant: 'retry' })
  }

  const jumpToQuote = () => {
    if (turn.quote?.page != null) useReaderStore.getState().requestScrollToPosition({ page: turn.quote.page, offset: 0 }, 'page')
  }

  return (
    <article className={s.turn} aria-labelledby={headingId} aria-busy={streaming || undefined} data-turn={turn.key}>
      {turn.quote ? (
        <figure className={s.quote}>
          <span className={s.quoteBracket} aria-hidden="true" />
          <blockquote className={s.quoteText}>{turn.quote.text}</blockquote>
          {turn.quote.page != null ? (
            <figcaption>
              <button type="button" className={s.locator} onClick={jumpToQuote}>
                {turn.quote.sectionNumber ? `§${turn.quote.sectionNumber}, ` : ''}p.{NBSP}
                {turn.quote.page + 1}
              </button>
            </figcaption>
          ) : null}
        </figure>
      ) : null}

      {turn.question ? (
        <h3 id={headingId} className={s.question}>
          {turn.question}
        </h3>
      ) : null}

      {answer.phase === 'not_sent' ? (
        <p className={s.problem}>
          <WarningCircleIcon size={14} aria-hidden="true" /> {answer.problem?.kind === 'not_sent' && answer.problem.offline ? 'Will send when you’re back online.' : 'Not sent.'}{' '}
          <button type="button" className={s.inlineAction} onClick={retry}>
            Try again
          </button>
        </p>
      ) : answer.phase === 'queued' ? (
        <p className={s.trail}>
          <ClockIcon size={14} aria-hidden="true" className={s.inlineIcon} /> Waiting for the paper to finish indexing. This will send when it’s
          ready.
        </p>
      ) : (
        <Trail answer={answer} />
      )}

      {generalOnly ? <p className={s.noteLine}>General background. Nothing in this answer comes from the paper.</p> : null}

      {answer.gaps && calloutsAllowed ? <GapCallout gaps={answer.gaps} level={answer.level} /> : null}

      {answer.text ? (
        <AnswerMarkdown
          text={answer.text}
          streaming={answer.phase === 'writing'}
          sources={answer.sources}
          external={answer.external}
          messageId={answer.messageId}
          className={s.answer}
        />
      ) : null}

      <Problem answer={answer} onRetry={retry} onRephrase={() => onRephrase(turn.question)} />

      {answer.knowledge ? <KnowledgeLine added={answer.knowledge.added} removed={answer.knowledge.removed} /> : null}

      {turn.answers.length > 1 ? (
        <div className={s.pager}>
          <IconButton label="Previous version" icon={CaretLeftIcon} size="xs" onClick={() => showVersion(turn.key, turn.active - 1)} disabled={turn.active === 0} noTooltip={turn.active === 0} />
          <span className={s.pagerText}>
            Version {turn.active + 1} of {turn.answers.length}
            {VARIANT_LABEL[answer.variant] ? ` · ${VARIANT_LABEL[answer.variant]}` : ''}
          </span>
          <IconButton
            label="Next version"
            icon={CaretRightIcon}
            size="xs"
            onClick={() => showVersion(turn.key, turn.active + 1)}
            disabled={turn.active === turn.answers.length - 1}
            noTooltip={turn.active === turn.answers.length - 1}
          />
        </div>
      ) : null}

      <Actions turn={turn} answer={answer} latest={latest} />

      {latest && answer.followups.length && !isLive(answer) ? (
        <ul className={s.followups} aria-label="Follow-up questions" role="list">
          {answer.followups.map((q) => (
            <li key={q}>
              <button type="button" className={s.suggestion} onClick={() => ask({ question: q })}>
                {q}
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </article>
  )
})
