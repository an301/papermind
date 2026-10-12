/**
 * The prerequisite note (DESIGN.md §5.16): an uncontained margin note saying
 * which ideas this answer builds on that haven't come up in the reader's
 * reading yet, a suggested order, and two ways on. It never blocks the answer.
 * Visible copy never says missing, gap, lacking or prerequisite.
 */
import { XIcon } from '@phosphor-icons/react'
import { useState } from 'react'
import { useAddKnowledge, useForgetKnowledge } from '../../../api/queries'
import type { ConceptRef, GapsFound, Level } from '../../../api/types'
import { Button } from '../../../components/Button'
import { CaretInsertGlyph } from '../../../components/glyphs'
import { IconButton } from '../../../components/IconButton'
import { listJoin } from '../../../lib/format'
import { useReaderStore } from '../../shared/readerStore'
import s from './Turn.module.css'
import { announce } from '../../../lib/announce'

const hidden = new Set<string>()

/**
 * The gaps worth naming: prerequisites, not the asked-about concept itself.
 * Technical names only direct ones (§5.16); Standard stops two steps out,
 * because it already assumes the foundations further down.
 */
export function namedGaps(gaps: GapsFound, level: Level): ConceptRef[] {
  const maxDepth = level === 'expert' ? 1 : level === 'intermediate' ? 2 : Infinity
  return gaps.gaps
    .filter((g) => g.depth > 0 && g.depth <= maxDepth)
    .map((g) => ({ concept_id: g.concept_id, name: g.name }))
}

/** Proper names that keep their capital in running text. */
const PROPER = /^(Adam|AdamW|Bayes|Bayes’|Bayesian|Gaussian|Markov|Bernoulli|Bradley|Kullback|Laplace|Monte|Euclidean|Hessian|Jacobian|Xavier|He|Polyak|Nesterov|Shannon|Boltzmann)\b/

/** "softmax", "attention mechanism" in a sentence; "BLEU", "ImageNet" and "Adam" keep their case. */
export function inSentence(name: string): string {
  const firstWord = name.split(/\s/)[0] ?? ''
  if (PROPER.test(name) || /[A-Z]/.test(firstWord.slice(1))) return name
  return name.charAt(0).toLowerCase() + name.slice(1)
}

function sentence(names: string[]): string {
  if (names.length === 1) return `This builds on ${names[0]}, which hasn’t come up in your reading yet.`
  if (names.length === 2) return `This builds on ${names[0]} and ${names[1]}, which haven’t come up in your reading yet.`
  return `This builds on ${names[0]}, ${names[1]} and ${names.length - 2} more ideas that haven’t come up in your reading yet.`
}

export function GapCallout({ gaps, level }: { gaps: GapsFound; level: Level }) {
  const concepts = namedGaps(gaps, level)
  const key = `${gaps.concept?.concept_id ?? ''}:${concepts.map((c) => c.concept_id).join(',')}`
  const [state, setState] = useState<'open' | 'hidden' | 'known' | 'failed'>(() => (hidden.has(key) ? 'hidden' : 'open'))
  const [expanded, setExpanded] = useState(false)
  const add = useAddKnowledge()
  const forget = useForgetKnowledge()

  if (!concepts.length || state === 'hidden') return null
  const names = concepts.map((c) => inSentence(c.name))
  const first = concepts.length === 1 ? inSentence(concepts[0]!.name) : null
  const ask = (text: string) => useReaderStore.getState().requestAsk(text)

  const markKnown = async () => {
    setState('known')
    try {
      await Promise.all(concepts.map((c) => add.mutateAsync(c.concept_id)))
    } catch {
      setState('failed')
    }
  }
  const undo = async () => {
    const results = await Promise.allSettled(concepts.map((c) => forget.mutateAsync(c.concept_id)))
    if (results.some((r) => r.status === 'rejected')) {
      // Still marked as known on the server: say so rather than pretend.
      announce('Couldn’t undo that. Try again.')
      return
    }
    setState('open')
  }

  if (state === 'known') {
    return (
      <p className={s.noteLine} role="status">
        Added {listJoin(names)} to what you know.{' '}
        <button type="button" className={s.inlineAction} onClick={undo}>
          Undo
        </button>
      </p>
    )
  }

  // Suggested order (teaching order), shown below Technical when there are at least two stops.
  // The path walks the same ideas the sentence names (plus the destination), so the two never disagree.
  const named = new Set(concepts.map((c) => c.concept_id))
  const order = gaps.teaching_order.filter((c) => named.has(c.concept_id) || c.concept_id === gaps.concept?.concept_id)
  const showPath = level !== 'expert' && order.length >= 2
  const target = gaps.concept?.concept_id
  const gapIds = new Set(gaps.gaps.map((g) => g.concept_id))
  const stops = expanded || order.length <= 4 ? order : [...order.slice(0, 2), null, order[order.length - 1]!]

  return (
    <aside className={s.callout} aria-label="Before this answer">
      <span className={s.calloutMark} aria-hidden="true">
        <CaretInsertGlyph size={16} />
      </span>
      <div className={s.calloutBody}>
        <div className={s.calloutRow}>
          <p className={s.calloutText}>{sentence(names)}</p>
          <IconButton
            label="Hide this note"
            icon={XIcon}
            size="xs"
            onClick={() => {
              hidden.add(key)
              setState('hidden')
            }}
          />
        </div>
        {showPath ? (
          <div className={s.path}>
            <span className={s.pathLabel}>Suggested order:</span>
            <ol className={s.stops} aria-label="Suggested order" role="list">
              {stops.map((stop, i) =>
                stop === null ? (
                  <li key="more" className={s.stop}>
                    <span className={s.connector} aria-hidden="true" />
                    <button type="button" className={s.inlineAction} onClick={() => setExpanded(true)}>
                      {order.length - 3} more
                    </button>
                  </li>
                ) : (
                  <li key={stop.concept_id} className={s.stop}>
                    {i > 0 ? <span className={s.connector} aria-hidden="true" /> : null}
                    {stop.concept_id === target ? (
                      <CaretInsertGlyph size={12} className={s.markTarget} />
                    ) : gapIds.has(stop.concept_id) ? (
                      <span className={s.markNotYet} aria-hidden="true" />
                    ) : (
                      <span className={s.markKnown} aria-hidden="true" />
                    )}
                    {stop.concept_id !== target && gapIds.has(stop.concept_id) ? (
                      <button
                        type="button"
                        className={s.stopName}
                        aria-label={`${stop.name}, not yet in what you know. Explain it`}
                        onClick={() => ask(`Explain ${stop.name}.`)}
                      >
                        {inSentence(stop.name)}
                      </button>
                    ) : (
                      <span className={s.stopText}>{inSentence(stop.name)}</span>
                    )}
                  </li>
                ),
              )}
            </ol>
          </div>
        ) : null}
        <div className={s.calloutActions}>
          <Button size="sm" onClick={() => ask(first ? `Explain ${first} before we go on.` : `Explain ${listJoin(names)} before we go on.`)}>
            {first ? `Explain ${first} first` : 'Explain these first'}
          </Button>
          <Button size="sm" variant="text" onClick={markKnown}>
            {first ? `I know ${first}` : 'I know these'}
          </Button>
        </div>
        {state === 'failed' ? <p className={s.noteLine}>Couldn’t save that. Try again.</p> : null}
      </div>
    </aside>
  )
}
