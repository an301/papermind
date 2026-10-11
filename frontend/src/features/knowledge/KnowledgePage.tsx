import { MagnifyingGlassIcon } from '@phosphor-icons/react'
import { useEffect, useMemo, useState } from 'react'
import { NetworkError } from '../../api/client'
import { useConceptAreas, useKnowledge } from '../../api/queries'
import type { ConceptArea, KnownConcept } from '../../api/types'
import p from '../../app/Page.module.css'
import { Button } from '../../components/Button'
import { Code } from '../../components/Code'
import { EmptyState } from '../../components/EmptyState'
import { ErrorState } from '../../components/ErrorState'
import { Skeleton } from '../../components/Skeleton'
import { Input } from '../../components/TextField'
import { clsx } from '../../lib/clsx'
import { plural, singleQuoted } from '../../lib/format'
import { prefersReducedMotion } from '../../lib/motion'
import { useDelayedFlag } from '../../lib/useDelayedFlag'
import { AddConcept } from './AddConcept'
import { AreasForm } from './AreasForm'
import { ConceptRow } from './ConceptRow'
import { useCorrections, type Correction } from './corrections'
import s from './KnowledgePage.module.css'

const OTHER: ConceptArea = { area_id: '__other', name: 'Other', description: '', concept_count: 0 }

interface Group {
  area: ConceptArea
  concepts: KnownConcept[]
}

/** Server list plus corrections still inside their Undo window, so a refetch can't undo them early. */
function withCorrections(list: readonly KnownConcept[], corrections: Record<string, Correction>): KnownConcept[] {
  const out = [...list]
  for (const c of Object.values(corrections)) {
    if (c.kind === 'forget' && c.previous && !out.some((k) => k.concept_id === c.conceptId)) out.push(c.previous)
  }
  return out.sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }))
}

/** Grouped by the 12 areas in data/concepts/areas.json, in that order; empty areas aren't shown (§4.5). */
function groupByArea(concepts: readonly KnownConcept[], areas: readonly ConceptArea[]): Group[] {
  const byArea = new Map<string, KnownConcept[]>()
  for (const k of concepts) {
    const id = k.area && areas.some((a) => a.area_id === k.area) ? k.area : OTHER.area_id
    byArea.set(id, [...(byArea.get(id) ?? []), k])
  }
  return [...areas, OTHER].flatMap((area) => {
    const list = byArea.get(area.area_id)
    return list?.length ? [{ area, concepts: list }] : []
  })
}

/**
 * "What you know" (DESIGN.md §4.5, §5.22): PaperMind's best guess at what the
 * reader knows, grouped by area, with the evidence behind each entry and a way
 * to correct anything that's wrong. Always a real list with headings; there is
 * no graph view.
 */
export function KnowledgePage() {
  const knowledge = useKnowledge()
  const areas = useConceptAreas()
  const corrections = useCorrections((st) => st.byConcept)
  const [filter, setFilter] = useState('')
  const [flash, setFlash] = useState<string | null>(null)

  const all = useMemo(() => withCorrections(knowledge.data ?? [], corrections), [knowledge.data, corrections])
  const query = filter.trim().toLowerCase()
  const shown = query ? all.filter((k) => k.name.toLowerCase().includes(query)) : all
  const groups = groupByArea(shown, areas.data ?? [])

  const loading = knowledge.isPending || (areas.isPending && !areas.isError)
  const showSkeleton = useDelayedFlag(loading)
  const empty = !loading && !knowledge.isError && all.length === 0

  // Jump to a row (after Add, or picking a concept that's already there).
  useEffect(() => {
    if (!flash) return undefined
    const el = document.getElementById(`row-${flash}`)
    el?.scrollIntoView({ block: 'center', behavior: prefersReducedMotion() ? 'auto' : 'smooth' })
    el?.focus({ preventScroll: true })
    const t = window.setTimeout(() => setFlash(null), 1200)
    return () => window.clearTimeout(t)
  }, [flash])

  return (
    <div className={p.page}>
      <div className={p.column}>
        <h1 className={p.title}>What you know</h1>
        <p className={p.subtitle}>
          PaperMind’s best guess from your questions. Change anything that’s wrong.
          <br />
          Only you can see this.
        </p>

        {knowledge.isError && !knowledge.data ? (
          <div className={s.section}>
            <ErrorState
              tone={knowledge.error instanceof NetworkError ? 'neutral' : 'danger'}
              action={
                <Button variant="secondary" busy={knowledge.isFetching} busyLabel="Trying…" onClick={() => void knowledge.refetch()}>
                  Try again
                </Button>
              }
            >
              {knowledge.error instanceof NetworkError ? (
                <>
                  Can’t reach the PaperMind server. If you’re running it yourself, start it with <Code>make dev</Code>.
                </>
              ) : (
                'Couldn’t load what you know. The server didn’t respond.'
              )}
            </ErrorState>
          </div>
        ) : loading ? (
          showSkeleton ? <KnowledgeSkeleton /> : null
        ) : empty ? (
          <div className={s.section}>
            <EmptyState title="This fills in as you read.">
              <AreasForm />
            </EmptyState>
          </div>
        ) : (
          <>
            <div className={s.toolbar}>
              <Input
                type="search"
                leadingIcon={MagnifyingGlassIcon}
                placeholder="Find a concept…"
                aria-label="Find a concept"
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Escape' && filter) {
                    e.preventDefault()
                    setFilter('')
                  }
                }}
                controlClassName={s.filter}
              />
              <AddConcept known={all} onJumpTo={(id) => {
                setFilter('')
                setFlash(id)
              }} />
              <p className={clsx(s.count, 'num')}>{plural(all.length, 'concept')}</p>
            </div>

            {query && shown.length === 0 ? (
              <p className={s.noMatch}>
                Nothing in what you know matches {singleQuoted(filter.trim())}.{' '}
                <Button variant="text" textSize="small" tone="ink" onClick={() => setFilter('')}>
                  Clear
                </Button>
              </p>
            ) : null}

            <div className={s.groups}>
              {groups.map((g) => (
                <section key={g.area.area_id} className={s.group} aria-labelledby={`area-${g.area.area_id}`}>
                  <h2 id={`area-${g.area.area_id}`} className={s.areaHeading}>
                    <span>{g.area.name}</span>
                    <span className={clsx(s.areaCount, 'num')} aria-label={plural(g.concepts.length, 'concept')}>
                      {g.concepts.length}
                    </span>
                  </h2>
                  <ul role="list" className={s.rows}>
                    {g.concepts.map((k) => (
                      <ConceptRow
                        key={k.concept_id}
                        concept={k}
                        correction={corrections[k.concept_id]}
                        areas={areas.data ?? []}
                        highlighted={flash === k.concept_id}
                      />
                    ))}
                  </ul>
                </section>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  )
}

/** 6 rows: a 15px bar at 30%, a 15px bar at 70%; static (§5.22). */
function KnowledgeSkeleton() {
  return (
    <div className={s.section} aria-busy="true" aria-label="Loading what you know">
      {Array.from({ length: 6 }, (_, i) => (
        <div key={i} className={s.skeletonRow}>
          <Skeleton width="30%" height={15} />
          <Skeleton width="70%" height={15} style={{ marginTop: 'var(--space-2)' }} />
        </div>
      ))}
    </div>
  )
}
