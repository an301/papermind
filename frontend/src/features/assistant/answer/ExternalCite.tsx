/**
 * A paper the answer mentions from Semantic Scholar ([P1], DESIGN.md §5.15).
 * Not a chip: there is no passage on the page and so no bracket. It reads as
 * "Bahdanau et al., 2014" and opens a card with the paper's TLDR.
 */
import { ArrowUpRightIcon } from '@phosphor-icons/react'
import { Popover } from 'radix-ui'
import type { ExternalRef } from '../../../api/types'
import { authorsCitation } from '../../../lib/format'
import { useReaderStore } from '../../shared/readerStore'
import { useComposerPrefill } from '../state/composer'
import s from './CitationChip.module.css'

export function ExternalCite({ paper }: { paper: ExternalRef }) {
  const label = authorsCitation(paper.authors, paper.year) || paper.title
  const byline = [paper.authors.slice(0, 3).join(', '), paper.venue, paper.year].filter(Boolean).join(' · ')

  const ask = () => {
    useComposerPrefill.getState().prefill(`How does this paper use ${label}?`)
    useReaderStore.getState().requestComposerFocus()
  }

  return (
    <Popover.Root>
      <Popover.Trigger asChild>
        <button type="button" className={s.external} aria-label={`${label}: ${paper.title}`}>
          {label}
        </button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content className={s.card} side="top" align="start" sideOffset={8} collisionPadding={16}>
          <p className={s.paperTitle}>{paper.title}</p>
          {byline ? <p className={s.cardPage}>{byline}</p> : null}
          {paper.tldr ? (
            <>
              <p className={s.tldrLabel}>TLDR</p>
              <p className={s.tldr}>{paper.tldr}</p>
            </>
          ) : null}
          <div className={s.cardActions}>
            <Popover.Close asChild>
              <button type="button" className={s.show} onClick={ask}>
                Ask about this paper
              </button>
            </Popover.Close>
            {paper.url ? (
              <a className={s.show} href={paper.url} target="_blank" rel="noopener noreferrer">
                Semantic Scholar <ArrowUpRightIcon size={12} aria-hidden="true" />
              </a>
            ) : null}
          </div>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  )
}
