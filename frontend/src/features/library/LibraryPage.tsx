import { MagnifyingGlassIcon } from '@phosphor-icons/react'
import { useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router'
import { NetworkError } from '../../api/client'
import { usePapers } from '../../api/queries'
import type { PaperSummary } from '../../api/types'
import p from '../../app/Page.module.css'
import { Button } from '../../components/Button'
import { Skeleton } from '../../components/Skeleton'
import { Code } from '../../components/Code'
import { ErrorState } from '../../components/ErrorState'
import { Input } from '../../components/TextField'
import { textLinkClass } from '../../components/TextLink'
import { useDelayedFlag } from '../../lib/useDelayedFlag'
import { getProgress, useProgressVersion } from '../../lib/readingProgress'
import { singleQuoted } from '../../lib/format'
import { AddPaperForm, ADD_FIELD_ID } from './AddPaperForm'
import { addArxiv } from './addFlow'
import { DropOverlay } from './DropOverlay'
import { PaperRow, UploadRow } from './PaperRow'
import { displayTitle } from './paperText'
import { isPendingRemoval } from './removals'
import { useUploads } from './uploads'
import s from './LibraryPage.module.css'

/** From this many papers up, a filter field appears above the list (§4.3). */
export const FILTER_FROM = 12
const SAMPLE_ID = '1706.03762'

/** Sort: last opened, then date added, newest first. A paper just added counts as just touched. */
function sortKey(paper: PaperSummary): number {
  const opened = getProgress(paper.paper_id)?.openedAt
  return Math.max(Date.parse(paper.added_at) || 0, opened ? Date.parse(opened) || 0 : 0)
}

export function LibraryPage() {
  const papers = usePapers()
  const uploads = useUploads((st) => st.uploads)
  const progressVersion = useProgressVersion()
  const [filter, setFilter] = useState('')
  const filterRef = useRef<HTMLInputElement>(null)
  // The papers already there on first load; rows that arrive later fade in (§6.1). Set during render
  // the first time data exists (React's "information from previous renders" pattern), never again.
  const [firstIds, setFirstIds] = useState<Set<string> | null>(null)
  if (papers.data && !firstIds) setFirstIds(new Set(papers.data.map((x) => x.paper_id)))

  const list = useMemo(() => {
    void progressVersion // re-sort when reading progress changes
    return (papers.data ?? []).filter((x) => !isPendingRemoval(x.paper_id)).sort((a, b) => sortKey(b) - sortKey(a))
  }, [papers.data, progressVersion])

  const query = filter.trim().toLowerCase()
  const shown = query
    ? list.filter((x) => displayTitle(x).toLowerCase().includes(query) || x.authors.some((a) => a.toLowerCase().includes(query)))
    : list

  const loading = papers.isPending
  const showSkeleton = useDelayedFlag(loading)
  const empty = !loading && !papers.isError && list.length === 0 && uploads.length === 0

  return (
    <div className={p.page}>
      <DropOverlay />
      <div className={p.column}>
        <h1 className={p.title}>{empty ? 'Start with a paper you’ve been meaning to read.' : 'Your papers'}</h1>
        <div className={s.add}>
          <AddPaperForm />
        </div>

        {empty ? <SampleLine /> : null}

        {papers.isError && !papers.data ? (
          <div className={s.listArea}>
            <ErrorState
              tone={papers.error instanceof NetworkError ? 'neutral' : 'danger'}
              action={
                <Button variant="secondary" busy={papers.isFetching} busyLabel="Trying…" onClick={() => void papers.refetch()}>
                  Try again
                </Button>
              }
            >
              {papers.error instanceof NetworkError ? (
                <>
                  Can’t reach the PaperMind server. If you’re running it yourself, start it with <Code>make dev</Code>.
                </>
              ) : (
                'Couldn’t load your papers. The server didn’t respond.'
              )}
            </ErrorState>
          </div>
        ) : loading ? (
          showSkeleton ? <LibrarySkeleton /> : null
        ) : empty ? null : (
          <section className={s.listArea} aria-labelledby="library-list">
            <h2 id="library-list" className={s.visuallyHidden}>
              {list.length === 1 ? '1 paper' : `${list.length} papers`}
            </h2>
            {list.length >= FILTER_FROM ? (
              <div className={s.filter}>
                <Input
                  ref={filterRef}
                  type="search"
                  leadingIcon={MagnifyingGlassIcon}
                  placeholder="Filter by title or author…"
                  aria-label="Filter your papers"
                  value={filter}
                  onChange={(e) => setFilter(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Escape' && filter) {
                      e.preventDefault()
                      setFilter('')
                    }
                  }}
                  controlClassName={s.filterControl}
                />
              </div>
            ) : null}
            {query && shown.length === 0 ? (
              <p className={s.noMatch}>
                No papers match {singleQuoted(filter.trim())}.{' '}
                <Button
                  variant="text"
                  textSize="small"
                  tone="ink"
                  onClick={() => {
                    setFilter('')
                    filterRef.current?.focus()
                  }}
                >
                  Clear filter
                </Button>
              </p>
            ) : null}
            <ul role="list" className={s.list}>
              {query ? null : uploads.map((u) => <UploadRow key={u.id} upload={u} />)}
              {shown.map((paper) => (
                <PaperRow key={paper.paper_id} paper={paper} isNew={!!firstIds && !firstIds.has(paper.paper_id)} />
              ))}
            </ul>
          </section>
        )}
      </div>
    </div>
  )
}

/** First run: "Or open Attention Is All You Need to see how it works." */
function SampleLine() {
  const navigate = useNavigate()
  const [busy, setBusy] = useState(false)
  return (
    <p className={s.sample}>
      Or open{' '}
      <a
        href={`/paper/${SAMPLE_ID}`}
        className={textLinkClass()}
        aria-busy={busy || undefined}
        onClick={async (e) => {
          e.preventDefault()
          if (busy) return
          setBusy(true)
          const outcome = await addArxiv(SAMPLE_ID)
          setBusy(false)
          // Already prepared on the server: open it. Otherwise its row is now preparing above.
          if (outcome === 'exists') navigate(`/paper/${SAMPLE_ID}`)
          else if (outcome === 'added') document.getElementById(ADD_FIELD_ID)?.focus()
        }}
        translate="no"
      >
        Attention Is All You Need
      </a>{' '}
      to see how it works.
    </p>
  )
}

/** Three rows matching the real layout; static fills, no shimmer (§5.5). */
function LibrarySkeleton() {
  return (
    <div className={s.listArea} aria-busy="true" aria-label="Loading your papers">
      {[0, 1, 2].map((i) => (
        <div key={i} className={s.skeletonRow}>
          <Skeleton width="60%" height={19} />
          <Skeleton width="30%" height={13} style={{ marginTop: 'var(--space-2)' }} />
          <Skeleton width="var(--strip-w)" height={12} style={{ marginTop: 'var(--space-2)' }} />
        </div>
      ))}
    </div>
  )
}
