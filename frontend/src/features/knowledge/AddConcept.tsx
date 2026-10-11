import { MagnifyingGlassIcon } from '@phosphor-icons/react'
import { useEffect, useId, useMemo, useRef, useState } from 'react'
import { useConceptAreas, useConceptSearch } from '../../api/queries'
import type { ConceptInfo, KnownConcept } from '../../api/types'
import { Button } from '../../components/Button'
import { Popover, PopoverContent, PopoverTrigger } from '../../components/Popover'
import { Input } from '../../components/TextField'
import { clsx } from '../../lib/clsx'
import { count, singleQuoted } from '../../lib/format'
import { addConcept } from './corrections'
import s from './AddConcept.module.css'

/**
 * "Add something you know" (§5.22): a combobox in a 320px popover. Results
 * show the concept and its area; Enter or click adds it. A concept already in
 * the list is shown as such and selecting it jumps to its row.
 */
export function AddConcept({ known, onJumpTo, block }: { known: readonly KnownConcept[]; onJumpTo: (conceptId: string) => void; block?: boolean }) {
  const [open, setOpen] = useState(false)
  const [instant, setInstant] = useState(false)
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="secondary"
          block={block}
          onPointerDown={() => setInstant(false)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') setInstant(true)
          }}
        >
          Add something you know
        </Button>
      </PopoverTrigger>
      <PopoverContent width="var(--combobox-w)" align="start" instant={instant} className={s.popover}>
        <ConceptSearch
          known={known}
          onPick={(c, already) => {
            setOpen(false)
            if (already) onJumpTo(c.concept_id)
            else {
              addConcept(c)
              onJumpTo(c.concept_id)
            }
          }}
        />
      </PopoverContent>
    </Popover>
  )
}

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value)
  useEffect(() => {
    const t = window.setTimeout(() => setV(value), ms)
    return () => window.clearTimeout(t)
  }, [value, ms])
  return v
}

function ConceptSearch({ known, onPick }: { known: readonly KnownConcept[]; onPick: (c: ConceptInfo, already: boolean) => void }) {
  const [q, setQ] = useState('')
  const query = useDebounced(q.trim(), 150)
  const search = useConceptSearch(query, 8)
  const areas = useConceptAreas()
  // The highlighted option, remembered per query so a new query starts from the top.
  const [cursor, setCursor] = useState({ query: '', index: 0 })
  const active = cursor.query === query ? cursor.index : 0
  const setActive = (fn: (i: number) => number) => setCursor({ query, index: fn(active) })
  const listId = useId()
  const inputRef = useRef<HTMLInputElement>(null)
  const knownIds = useMemo(() => new Set(known.map((k) => k.concept_id)), [known])
  const total = areas.data?.reduce((n, a) => n + a.concept_count, 0)
  const results = query ? (search.data ?? []) : []
  const areaName = (id?: string | null) => areas.data?.find((a) => a.area_id === id)?.name ?? ''

  const pick = (c: ConceptInfo | undefined) => {
    if (c) onPick(c, knownIds.has(c.concept_id))
  }

  return (
    <div className={s.search}>
      <Input
        ref={inputRef}
        autoFocus
        type="text"
        role="combobox"
        aria-expanded={results.length > 0}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={results[active] ? `${listId}-${active}` : undefined}
        aria-label="Search concepts"
        leadingIcon={MagnifyingGlassIcon}
        placeholder={total ? `Search ${count(total)} concepts…` : 'Search concepts…'}
        value={q}
        spellCheck={false}
        autoComplete="off"
        onChange={(e) => setQ(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown') {
            e.preventDefault()
            setActive((i) => Math.min(results.length - 1, i + 1))
          } else if (e.key === 'ArrowUp') {
            e.preventDefault()
            setActive((i) => Math.max(0, i - 1))
          } else if (e.key === 'Enter') {
            e.preventDefault()
            pick(results[active])
          }
        }}
      />
      <ul id={listId} role="listbox" aria-label="Concepts" className={s.list}>
        {results.map((c, i) => {
          const already = knownIds.has(c.concept_id)
          return (
            // Options aren't focusable: the combobox input handles the keys (ARIA 1.2 combobox pattern).
            // oxlint-disable-next-line jsx-a11y/click-events-have-key-events
            <li
              key={c.concept_id}
              id={`${listId}-${i}`}
              role="option"
              aria-selected={i === active}
              className={clsx(s.option, i === active && s.active)}
              onPointerMove={() => setActive(() => i)}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => pick(c)}
            >
              <span className={s.optionName}>{c.name}</span>
              <span className={s.optionArea}>{already ? 'In what you know' : areaName(c.area)}</span>
            </li>
          )
        })}
      </ul>
      {query && search.isSuccess && !search.isPlaceholderData && results.length === 0 ? (
        <p className={s.empty} role="status">
          No concept called {singleQuoted(query)} in the map yet.
        </p>
      ) : null}
      {query && search.isError ? (
        <p className={s.empty} role="status">
          Couldn’t search the concept map just now.
        </p>
      ) : null}
    </div>
  )
}
