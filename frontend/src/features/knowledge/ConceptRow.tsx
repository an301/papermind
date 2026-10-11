import { DotsThreeIcon } from '@phosphor-icons/react'
import { useState } from 'react'
import type { ConceptArea, KnownConcept } from '../../api/types'
import { Button } from '../../components/Button'
import { IconButton } from '../../components/IconButton'
import { Menu, MenuItem, MenuSeparator } from '../../components/Menu'
import { TextLink } from '../../components/TextLink'
import { clsx } from '../../lib/clsx'
import { NBSP } from '../../lib/format'
import { correctionText, forget, markKnown, retry, undo, type Correction } from './corrections'
import { conceptState, evidenceLines, type EvidenceLine } from './knowledgeText'
import s from './KnowledgePage.module.css'

export interface ConceptRowProps {
  concept: KnownConcept
  correction?: Correction
  areas: readonly ConceptArea[]
  /** Briefly marks a row the reader just jumped to (from Add). */
  highlighted?: boolean
}

/**
 * One concept (§5.22): name and state word on line 1; the newest evidence in
 * Literata on line 2, or the result of a correction for 6 s. Not boxed; the
 * hover background is the only container.
 */
export function ConceptRow({ concept, correction, areas, highlighted }: ConceptRowProps) {
  const [expanded, setExpanded] = useState(false)
  const lines = evidenceLines(concept, areas)
  const state = conceptState(concept)
  const nameId = `concept-${concept.concept_id}`
  const older = lines.slice(1)
  return (
    <li className={clsx(s.row, highlighted && s.rowFlash)} aria-labelledby={nameId} id={`row-${concept.concept_id}`} tabIndex={-1}>
      <div className={s.line1}>
        <h3 id={nameId} className={s.name}>
          {concept.name}
        </h3>
        <span className={s.state}>{correction?.kind === 'forget' && correction.status === 'pending' ? '' : state}</span>
        <div className={s.rowMenu}>
          <Menu trigger={<IconButton label={`Change ${concept.name}`} icon={DotsThreeIcon} noTooltip />}>
            {concept.source !== 'self_reported' ? <MenuItem onSelect={() => markKnown(concept)}>I know this</MenuItem> : null}
            {concept.source !== 'self_reported' ? <MenuSeparator /> : null}
            <MenuItem onSelect={() => forget(concept)}>Forget this</MenuItem>
          </Menu>
        </div>
      </div>
      {correction ? (
        <p className={s.result}>
          {correctionText(correction)}{' '}
          {correction.status === 'failed' ? (
            <Button variant="text" textSize="small" tone="ink" className={s.inlineButton} onClick={() => retry(concept.concept_id)}>
              Try again
            </Button>
          ) : (
            <Button variant="text" textSize="small" tone="ink" className={s.inlineButton} onClick={() => undo(concept.concept_id)}>
              Undo
            </Button>
          )}
        </p>
      ) : (
        <>
          {lines[0] ? <Evidence line={lines[0]} /> : null}
          {older.length ? (
            expanded ? (
              older.map((l, i) => <Evidence key={i} line={l} />)
            ) : (
              <Button variant="text" textSize="small" flush className={s.more} onClick={() => setExpanded(true)}>
                Show {older.length} more
              </Button>
            )
          ) : null}
        </>
      )}
    </li>
  )
}

function Evidence({ line }: { line: EvidenceLine }) {
  return (
    <p className={s.evidence}>
      {line.lead}
      {line.quote ? (
        <>
          {' '}
          <q className={s.quote}>{line.quote}</q>
        </>
      ) : null}
      {line.tail}
      {line.paper ? (
        <>
          {line.lead === 'Came up while reading' ? ' ' : ' in '}
          <TextLink quiet to={`/paper/${encodeURIComponent(line.paper.paperId)}`} className={s.locator}>
            <cite translate="no">{line.paper.label}</cite>
            {line.paper.section ? `, §${line.paper.section}` : ''}
            {` ·${NBSP}${line.date}`}
          </TextLink>
        </>
      ) : (
        <span className={s.locator}>{` ·${NBSP}${line.date}`}</span>
      )}
    </p>
  )
}
