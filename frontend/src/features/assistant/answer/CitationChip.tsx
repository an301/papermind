/**
 * A citation chip (DESIGN.md §5.15): the pencil numeral that matches the
 * bracket on the page, and where the passage is. Hover opens the passage card;
 * click (or Enter) jumps the PDF to the passage and re-inks its bracket.
 */
import { HoverCard } from 'radix-ui'
import { useState, type KeyboardEvent, type MouseEvent } from 'react'
import type { SourceRef } from '../../../api/types'
import { Kbd } from '../../../components/Kbd'
import { announce } from '../../../lib/announce'
import { NBSP } from '../../../lib/format'
import { useReaderStore } from '../../shared/readerStore'
import s from './CitationChip.module.css'

/** "§3.2.1", "§3.2.1 p. 4", "p. 4", "pp. 4–5" (§5.15 location rule). */
export function chipLocation(source: SourceRef, withPage: boolean): string {
  const pages =
    source.page_end > source.page_start
      ? `pp.${NBSP}${source.page_start + 1}–${source.page_end + 1}`
      : `p.${NBSP}${source.page_start + 1}`
  if (!source.section_number) return pages
  return withPage ? `§${source.section_number}${NBSP}${pages}` : `§${source.section_number}`
}

/** "Citation 1: section 3.2.1, page 4". The "§" is spelled out. */
export function chipName(source: SourceRef, numeral: number): string {
  const where = source.section_number ? `section ${source.section_number}, ` : ''
  const page = source.page_end > source.page_start ? `pages ${source.page_start + 1} to ${source.page_end + 1}` : `page ${source.page_start + 1}`
  return `Citation ${numeral}: ${where}${page}`
}

/** The snippet with the cited sentence in weight 600, when the backend sent one. */
function Snippet({ source }: { source: SourceRef }) {
  const text = source.snippet
  const sentence = source.cited_sentence?.trim()
  if (sentence) {
    const at = text.indexOf(sentence.slice(0, 60))
    if (at >= 0) {
      return (
        <>
          {at > 0 ? `…${text.slice(Math.max(0, at - 80), at)}` : ''}
          <strong>{sentence}</strong>
        </>
      )
    }
    return <strong>{sentence}</strong>
  }
  return <>{text}…</>
}

export interface CitationChipProps {
  source: SourceRef
  numeral: number
  messageId: string | null
  withPage: boolean
}

export function CitationChip({ source, numeral, messageId, withPage }: CitationChipProps) {
  const active = useReaderStore((st) => st.activeCitation?.chunkId === source.chunk_id && st.activeCitation?.messageId === messageId)
  const [open, setOpen] = useState(false)

  const jump = (viaKeyboard: boolean) => {
    setOpen(false)
    useReaderStore
      .getState()
      .requestScrollToChunk(source.chunk_id, { label: source.label, messageId, viaKeyboard })
      .then((chunk) => {
        if (chunk) {
          const where = chunk.section_number ? `section ${chunk.section_number}, ` : ''
          announce(`Showing ${where}page ${chunk.page_start + 1}`)
        }
      })
      .catch(() => announce('Couldn’t find that passage in the paper.'))
  }

  const onClick = (e: MouseEvent) => jump(e.detail === 0)
  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key === 'Escape') setOpen(false)
  }

  return (
    <HoverCard.Root open={open} onOpenChange={setOpen} openDelay={400} closeDelay={150}>
      <HoverCard.Trigger asChild>
        <button
          type="button"
          className={s.chip}
          data-active={active || undefined}
          data-chip-message={messageId ?? ''}
          data-chip-label={source.label}
          aria-label={chipName(source, numeral)}
          onClick={onClick}
          onKeyDown={onKeyDown}
          onFocus={(e) => {
            if (e.currentTarget.matches(':focus-visible')) setOpen(true)
          }}
          onBlur={() => setOpen(false)}
        >
          <span className={s.numeral}>{numeral}</span>
          <span className={s.where}>{chipLocation(source, withPage)}</span>
        </button>
      </HoverCard.Trigger>
      <HoverCard.Portal>
        <HoverCard.Content className={s.card} side="top" align="start" sideOffset={8} collisionPadding={16}>
          <p className={s.cardTitle}>
            {source.section_number ? `Section ${source.section_number} · ` : ''}
            {source.section_title}
          </p>
          <p className={s.cardPage}>
            {source.page_end > source.page_start ? `Pages ${source.page_start + 1}–${source.page_end + 1}` : `Page ${source.page_start + 1}`}
          </p>
          <p className={s.cardText}>
            <Snippet source={source} />
          </p>
          <button type="button" className={s.show} onClick={() => jump(false)}>
            Show in paper <Kbd>↵</Kbd>
          </button>
        </HoverCard.Content>
      </HoverCard.Portal>
    </HoverCard.Root>
  )
}
