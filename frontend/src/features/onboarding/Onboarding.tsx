import { useEffect, useId, useRef, useState } from 'react'
import { api } from '../../api/client'
import { useAddOnboardingAreas, useConceptAreas, useUpdateUser, useUser } from '../../api/queries'
import type { Level } from '../../api/types'
import { Button } from '../../components/Button'
import { Checkbox, Radio } from '../../components/Choice'
import { Skeleton } from '../../components/Skeleton'
import { Tooltip } from '../../components/Tooltip'
import { announce } from '../../lib/announce'
import { clsx } from '../../lib/clsx'
import { smarten } from '../../lib/format'
import { useReaderStore } from '../shared/readerStore'
import { LEVEL_OPTIONS } from './levels'
import s from './Onboarding.module.css'

export interface OnboardingProps {
  /**
   * Called when the reader finishes or skips (after the writes are sent). The
   * panel moves focus to the composer here (§4.6).
   */
  onDone?: (outcome: 'done' | 'skipped') => void
  /** 'panel' (the assistant's first run) or 'inline' (Settings → What you know). */
  context?: 'panel' | 'inline'
  className?: string
}

/**
 * First-run calibration, inline and skippable; never a modal or carousel
 * (DESIGN.md §4.6). Step 1 sets where answers start; step 2 marks whole
 * areas as comfortable. Step 2 replaces step 1 in place, with no animation.
 *
 * Writes: Continue → PATCH /users/{id} {level}. Done → POST knowledge/areas,
 * then PATCH {onboarded: true}. Skip → PATCH {onboarded: true} only.
 */
export function Onboarding({ onDone, context = 'panel', className }: OnboardingProps) {
  const [step, setStep] = useState<1 | 2>(1)
  const stepRef = useRef<HTMLDivElement>(null)
  const first = useRef(true)

  // Moving to step 2 puts focus at its question, so keyboard and screen-reader users land in the new step.
  useEffect(() => {
    if (first.current) {
      first.current = false
      return
    }
    stepRef.current?.focus()
  }, [step])

  return (
    <section className={clsx(s.onboarding, context === 'inline' && s.inline, className)} aria-labelledby="onboarding-title">
      {context === 'panel' ? (
        <h2 id="onboarding-title" className={s.heading}>
          Before your first question
        </h2>
      ) : (
        <h3 id="onboarding-title" className={s.heading}>
          Before your first question
        </h3>
      )}
      <div ref={stepRef} tabIndex={-1} className={s.step}>
        {step === 1 ? <LevelStep onContinue={() => setStep(2)} onSkip={() => onDone?.('skipped')} /> : <AreasStep onDone={onDone} />}
      </div>
    </section>
  )
}

function LevelStep({ onContinue, onSkip }: { onContinue: () => void; onSkip: () => void }) {
  const user = useUser()
  const update = useUpdateUser()
  const [level, setLevel] = useState<Level>(user.data?.level ?? 'intermediate')
  const name = useId()
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault()
        if (level !== user.data?.level) update.mutate({ level })
        // Onboarding in the Reader: the paper open now should start at this depth
        // too, not only papers opened later.
        const reader = useReaderStore.getState()
        if (reader.paperId && reader.level !== level) {
          reader.setLevel(level)
          if (reader.session) {
            void api(`/sessions/${encodeURIComponent(reader.session.session_id)}`, { method: 'PATCH', body: { level } }).catch(() => {})
          }
        }
        onContinue()
      }}
    >
      <fieldset className={s.fieldset}>
        <legend className={s.question}>Where should answers start?</legend>
        <div className={s.options}>
          {LEVEL_OPTIONS.map((o) => (
            <Radio
              key={o.value}
              name={name}
              value={o.value}
              checked={level === o.value}
              onChange={() => setLevel(o.value)}
              label={o.label}
              description={o.description}
              strong
            />
          ))}
        </div>
      </fieldset>
      <p className={s.note}>This only sets where answers start. You can change it for any question.</p>
      <div className={s.actions}>
        <Button type="submit" variant="primary">
          Continue
        </Button>
        <Button
          variant="text"
          onClick={() => {
            update.mutate({ onboarded: true })
            onSkip()
          }}
        >
          Skip for now
        </Button>
      </div>
    </form>
  )
}

function AreasStep({ onDone }: { onDone?: (outcome: 'done' | 'skipped') => void }) {
  const areas = useConceptAreas()
  const addAreas = useAddOnboardingAreas()
  const update = useUpdateUser()
  const [chosen, setChosen] = useState<Set<string>>(new Set())
  const [failed, setFailed] = useState(false)
  const descId = useId()

  async function done() {
    setFailed(false)
    try {
      if (chosen.size) await addAreas.mutateAsync([...chosen])
      await update.mutateAsync({ onboarded: true })
      if (chosen.size) announce(chosen.size === 1 ? 'Added 1 area to what you know.' : `Added ${chosen.size} areas to what you know.`)
      onDone?.('done')
    } catch {
      setFailed(true)
    }
  }

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault()
        void done()
      }}
    >
      <fieldset className={s.fieldset} aria-describedby={descId}>
        <legend className={s.question}>Which of these are you comfortable with?</legend>
        <p id={descId} className={s.note}>
          Answers won’t stop to explain these unless you ask. Optional.
        </p>
        <div className={s.areas}>
          {areas.isPending
            ? Array.from({ length: 12 }, (_, i) => (
                <div key={i} className={s.areaSkeleton}>
                  <Skeleton width="16px" height={16} />
                  <Skeleton width={`${40 + ((i * 17) % 35)}%`} height={14} />
                </div>
              ))
            : areas.isError
              ? <p className={s.note}>The list of areas isn’t available right now. You can add what you know later, from What you know.</p>
              : areas.data.map((a) => (
                  <Tooltip key={a.area_id} label={smarten(a.description)} side="right" align="center">
                    <span className={s.areaRow}>
                      <Checkbox
                        label={a.name}
                        checked={chosen.has(a.area_id)}
                        aria-description={smarten(a.description)}
                        onChange={(e) => {
                          const next = new Set(chosen)
                          if (e.target.checked) next.add(a.area_id)
                          else next.delete(a.area_id)
                          setChosen(next)
                        }}
                      />
                    </span>
                  </Tooltip>
                ))}
        </div>
      </fieldset>
      {failed ? (
        <p className={s.error} role="alert">
          Couldn’t save that. Try again, or skip for now.
        </p>
      ) : null}
      <div className={s.actions}>
        <Button type="submit" variant="primary" busy={addAreas.isPending || update.isPending} busyLabel="Saving…">
          Done
        </Button>
        <Button
          variant="text"
          onClick={() => {
            update.mutate({ onboarded: true })
            onDone?.('skipped')
          }}
        >
          Skip
        </Button>
      </div>
    </form>
  )
}
