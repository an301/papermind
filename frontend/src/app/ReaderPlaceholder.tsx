import { ArrowLeftIcon } from '@phosphor-icons/react'
import { useEffect } from 'react'
import { useParams } from 'react-router'
import { isNotFound } from '../api/client'
import { usePaper } from '../api/queries'
import { ButtonLink } from '../components/Button'
import { ErrorState } from '../components/ErrorState'
import { displayTitle } from '../features/library/paperText'
import { Onboarding, useNeedsOnboarding } from '../features/onboarding'
import { recordOpen } from '../lib/readingProgress'
import s from './ReaderPlaceholder.module.css'

/**
 * Stands in for the Reader until src/features/reader replaces it: the 48px
 * top bar with the way back and the paper's title, the PDF column's --canvas
 * desk, and a 400px assistant column that already runs first-run onboarding
 * (DESIGN.md §4.6), so that flow works end to end before the panel exists.
 * It records the open so the Library's ordering works.
 */
export function ReaderPlaceholder() {
  const { paperId = '' } = useParams()
  const paper = usePaper(paperId)
  const summary = paper.data?.summary
  const title = summary ? displayTitle(summary) : ''
  const needsOnboarding = useNeedsOnboarding()

  useEffect(() => {
    if (summary?.status === 'ready') recordOpen(paperId)
  }, [paperId, summary?.status])

  useEffect(() => {
    if (title) document.title = `${title} · PaperMind`
  }, [title])

  return (
    <div className={s.reader}>
      <header className={s.topbar}>
        <ButtonLink to="/" variant="text" icon={<ArrowLeftIcon size={16} aria-hidden="true" />}>
          Your papers
        </ButtonLink>
        <h1 className={s.title} translate="no">
          {title}
        </h1>
      </header>
      <div className={s.body}>
        <main className={s.desk} aria-label="Paper">
          {paper.isError && isNotFound(paper.error) ? (
            <div className={s.message}>
              <ErrorState
                icon={null}
                action={
                  <ButtonLink to="/" variant="secondary">
                    Back to your papers
                  </ButtonLink>
                }
              >
                This paper isn’t in your papers anymore.
              </ErrorState>
            </div>
          ) : null}
        </main>
        <aside className={s.panel} aria-label="Assistant">
          {needsOnboarding ? <Onboarding /> : null}
        </aside>
      </div>
    </div>
  )
}
