/**
 * The agent activity trail (DESIGN.md §5.11): one live line while the answer
 * is worked out, then a one-sentence summary with the steps behind a
 * disclosure. No spinner, shimmer or animated ellipsis: the line changes text.
 */
import { CaretDownIcon, CaretRightIcon, WarningIcon } from '@phosphor-icons/react'
import { useEffect, useId, useState } from 'react'
import { expandedSteps, liveLine, partialFailure, seconds, slowReason, stepLine, summaryLine } from '../state/trail'
import type { Answer } from '../state/types'
import { isLive } from '../state/types'
import s from './Turn.module.css'

/** Re-render once a second while live, so the elapsed time and the 300 ms first line appear. */
function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!active) return
    const first = window.setTimeout(() => setNow(Date.now()), 320)
    const t = window.setInterval(() => setNow(Date.now()), 1000)
    return () => {
      window.clearTimeout(first)
      window.clearInterval(t)
    }
  }, [active])
  return now
}

export function Trail({ answer }: { answer: Answer }) {
  const live = isLive(answer) && answer.phase !== 'answered'
  const now = useNow(live)
  const [open, setOpen] = useState(false)
  const listId = useId()

  if (live) {
    const elapsed = Math.max(0, now - answer.startedAt)
    const line = answer.phase === 'queued' ? null : liveLine(answer, elapsed)
    if (!line) return <p className={s.trail} aria-hidden="true">&nbsp;</p>
    const slow = slowReason(answer, elapsed)
    return (
      <p className={s.trail}>
        {line}
        {elapsed >= 8000 ? ` ${seconds(elapsed).replace(/\.\d/, '')}` : ''}
        {slow ? ` ${slow}` : ''}
      </p>
    )
  }

  // Finished, failed, stopped: the summary stays on the answer, collapsed.
  const steps = expandedSteps(answer)
  if (!steps.length && !answer.text) return null
  const fallbackMs = answer.endedAt && answer.startedAt ? answer.endedAt - answer.startedAt : null
  const summary = summaryLine(answer, fallbackMs)
  const partial = partialFailure(answer)

  return (
    <div className={s.trailBlock}>
      {steps.length ? (
        <button
          type="button"
          className={s.trailToggle}
          aria-expanded={open}
          aria-controls={listId}
          aria-label={`${summary}, ${open ? 'hide' : 'show'} steps`}
          onClick={() => setOpen((o) => !o)}
        >
          <span>{summary}</span>
          {open ? <CaretDownIcon size={12} aria-hidden="true" /> : <CaretRightIcon size={12} aria-hidden="true" />}
        </button>
      ) : (
        <p className={s.trail}>{summary}</p>
      )}
      {open ? (
        <ol id={listId} className={s.steps} role="list">
          {steps.map((step) => (
            <li key={step.callId} className={s.step}>
              {step.status === 'failed' ? <WarningIcon size={12} aria-label="Failed" className={s.stepIcon} /> : null}
              <span>{stepLine(step)}</span>
              {step.durationMs != null ? <span className={s.stepTime}> ({seconds(step.durationMs)})</span> : null}
            </li>
          ))}
        </ol>
      ) : null}
      {partial ? <p className={s.trailNote}>{partial}</p> : null}
    </div>
  )
}
