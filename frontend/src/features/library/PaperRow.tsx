import { CheckIcon, DotsThreeIcon, WarningCircleIcon, WarningIcon } from '@phosphor-icons/react'
import { useId } from 'react'
import { Link, useNavigate } from 'react-router'
import { canRemovePaper, useHealth } from '../../api/queries'
import type { PaperSummary } from '../../api/types'
import { Button } from '../../components/Button'
import { IconButton } from '../../components/IconButton'
import { Menu, MenuItem, MenuSeparator } from '../../components/Menu'
import { ProgressBar } from '../../components/ProgressBar'
import { announce } from '../../lib/announce'
import { copyText } from '../../lib/clipboard'
import { clsx } from '../../lib/clsx'
import { megabytes, NBSP, pageLabel } from '../../lib/format'
import { getProgress, type ReadingProgress } from '../../lib/readingProgress'
import { addArxiv, setHint } from './addFlow'
import { Crossfade } from './Crossfade'
import { PageStrip } from './PageStrip'
import { arxivIdOf, canOpenEarly, canRetry, displayTitle, failureText, hasRealTitle, ingestSteps, metaLine } from './paperText'
import { useRecentlyReady } from './recentlyReady'
import { cancelIngestion, removeFailed, removeWithUndo } from './removals'
import { cancelUpload, removeFailedUpload, retryUpload, type Upload } from './uploads'
import s from './PaperRow.module.css'

const paperPath = (id: string) => `/paper/${encodeURIComponent(id)}`

/** "You stopped at §3.2" · "You stopped on p. 4" · "Not opened". */
function whereYouStopped(progress: ReadingProgress | null): string {
  if (!progress) return 'Not opened'
  if (progress.lastSection) return `You stopped at §${progress.lastSection}`
  return `You stopped on ${pageLabel(progress.lastPage)}`
}

export function PaperRow({ paper, isNew }: { paper: PaperSummary; isNew?: boolean }) {
  if (paper.status === 'failed') return <FailedRow paper={paper} isNew={isNew} />
  if (paper.status === 'ready') return <ReadyRow paper={paper} isNew={isNew} />
  return <WorkingRow paper={paper} isNew={isNew} />
}

/**
 * A ready paper: the whole row is one link, so there are no dead zones; the
 * overflow menu sits in the right column, outside the link (§5.3).
 */
function ReadyRow({ paper, isNew }: { paper: PaperSummary; isNew?: boolean }) {
  const ids = useId()
  const progress = getProgress(paper.paper_id)
  const justReady = useRecentlyReady((st) => paper.paper_id in st.ids)
  const title = displayTitle(paper)
  const meta = metaLine(paper)
  const status = justReady ? 'ready' : whereYouStopped(progress)
  return (
    <li className={clsx(s.row, isNew && s.entering)}>
      <Link
        to={paperPath(paper.paper_id)}
        className={s.link}
        aria-labelledby={`${ids}-t`}
        aria-describedby={`${ids}-m ${ids}-s`}
      >
        <div className={s.main}>
          <h2 id={`${ids}-t`} className={s.title} translate="no" title={title.length > 90 ? title : undefined}>
            <Crossfade id={title} className={s.titleStack} itemClassName={s.clamp}>
              {title}
            </Crossfade>
          </h2>
          {meta ? (
            <p id={`${ids}-m`} className={clsx(s.meta, 'num')}>
              {meta}
            </p>
          ) : null}
          {progress && paper.num_pages > 0 ? (
            <div className={s.stripLine}>
              <PageStrip numPages={paper.num_pages} progress={progress} />
              <span className={clsx(s.meta, 'num')} aria-hidden="true">
                {pageLabel(progress.lastPage)} of {paper.num_pages}
              </span>
            </div>
          ) : null}
        </div>
        <p id={`${ids}-s`} className={s.status}>
          <Crossfade id={status}>
            {status === 'ready' ? (
              <span className={s.ready}>
                <CheckIcon size={16} aria-hidden="true" className={s.success} />
                Ready
              </span>
            ) : (
              status
            )}
          </Crossfade>
        </p>
      </Link>
      <RowMenu paper={paper} title={title} />
    </li>
  )
}

function RowMenu({ paper, title }: { paper: PaperSummary; title: string }) {
  const navigate = useNavigate()
  const canRemove = canRemovePaper(useHealth().data, paper.paper_id)
  return (
    <div className={s.menu}>
      <Menu trigger={<IconButton label={`More for ${title}`} icon={DotsThreeIcon} noTooltip />}>
        <MenuItem onSelect={() => navigate(paperPath(paper.paper_id))}>Open</MenuItem>
        {paper.source === 'arxiv' ? (
          <MenuItem
            onSelect={() => {
              void copyText(`https://arxiv.org/abs/${arxivIdOf(paper.paper_id)}`).then((ok) => ok && announce('Copied'))
            }}
          >
            Copy arXiv link
          </MenuItem>
        ) : null}
        {canRemove ? (
          <>
            <MenuSeparator />
            <MenuItem danger onSelect={() => removeWithUndo(paper)}>
              Remove from your papers
            </MenuItem>
          </>
        ) : null}
      </Menu>
    </div>
  )
}

/**
 * A paper being prepared. The steps line grows as work happens; the title
 * turns into a link (and the right column says "Open now") once the PDF is
 * on the server (§5.4 "Read early").
 */
function WorkingRow({ paper, isNew }: { paper: PaperSummary; isNew?: boolean }) {
  const ids = useId()
  const title = displayTitle(paper)
  const real = hasRealTitle(paper)
  const meta = metaLine(paper)
  const steps = ingestSteps(paper)
  const open = canOpenEarly(paper)
  const titleNode = (
    <Crossfade id={title} className={s.titleStack} itemClassName={s.clamp}>
      {title}
    </Crossfade>
  )
  return (
    <li className={clsx(s.row, s.static, isNew && s.entering)} aria-labelledby={`${ids}-t`}>
      <div className={s.box}>
        <div className={s.main}>
          <h2 id={`${ids}-t`} className={clsx(s.title, !real && s.placeholder)} translate="no">
            {open ? (
              <Link to={paperPath(paper.paper_id)} className={s.titleLink}>
                {titleNode}
              </Link>
            ) : (
              titleNode
            )}
          </h2>
          {meta ? <p className={clsx(s.meta, 'num')}>{meta}</p> : null}
        </div>
        <p className={s.status}>
          {open ? (
            <Link to={paperPath(paper.paper_id)} className={s.statusLink}>
              Open now
            </Link>
          ) : (
            'Getting it ready'
          )}
        </p>
        <div className={clsx(s.steps, 'num')}>
          <ol className={s.stepList} aria-label="Steps">
            {steps.done.map((label) => (
              <li key={label} className={clsx(s.step, s.stepDone)}>
                <CheckIcon size={12} aria-hidden="true" className={s.stepCheck} />
                {label}
              </li>
            ))}
            {steps.current ? (
              <li key={steps.current.replace(/\d+/g, '#')} className={clsx(s.step, s.stepCurrent)} aria-current="step">
                {steps.current}
              </li>
            ) : null}
          </ol>
          <span className={s.stepsNarrow} aria-hidden="true">
            {steps.current}
            <span className={s.stepCount}>
              {' '}
              {steps.done.length} of {steps.total}
              {NBSP}steps done
            </span>
          </span>
          <Button variant="text" textSize="small" className={s.inlineAction} onClick={() => cancelIngestion(paper)}>
            Cancel
          </Button>
        </div>
      </div>
    </li>
  )
}

/** Ingestion failed: the reason, in the row, with what can be done about it (§5.25). */
function FailedRow({ paper, isNew }: { paper: PaperSummary; isNew?: boolean }) {
  const ids = useId()
  const title = displayTitle(paper)
  const meta = metaLine(paper)
  const scan = paper.error_code === 'no_text'
  return (
    <li className={clsx(s.row, s.static, isNew && s.entering)} aria-labelledby={`${ids}-t`}>
      <div className={s.box}>
        <div className={s.main}>
          <h2 id={`${ids}-t`} className={clsx(s.title, !hasRealTitle(paper) && s.placeholder)} translate="no">
            {title}
          </h2>
          {meta ? <p className={clsx(s.meta, 'num')}>{meta}</p> : null}
        </div>
        <p className={s.status}>Not added</p>
        <div className={s.failure}>
          {scan ? (
            <WarningIcon size={16} aria-hidden="true" className={s.warnIcon} />
          ) : (
            <WarningCircleIcon size={16} aria-hidden="true" className={s.dangerIcon} />
          )}
          <span className={s.failureText}>{failureText(paper)}</span>
        </div>
        <div className={s.actions}>
          {canRetry(paper) ? (
            <Button variant="text" textSize="small" tone="ink" flush onClick={() => void addArxiv(arxivIdOf(paper.paper_id))}>
              Try again
            </Button>
          ) : null}
          <Button variant="text" textSize="small" flush={!canRetry(paper)} onClick={() => removeFailed(paper)}>
            Remove
          </Button>
        </div>
      </div>
    </li>
  )
}

/** A PDF still on its way up: filename as the placeholder title and byte progress where the strip goes. */
export function UploadRow({ upload }: { upload: Upload }) {
  const ids = useId()
  const name = upload.name.replace(/\.pdf$/i, '')
  const sent = Math.round(upload.fraction * 100)
  return (
    <li className={clsx(s.row, s.static, s.entering)} aria-labelledby={`${ids}-t`}>
      <div className={s.box}>
        <div className={s.main}>
          <h2 id={`${ids}-t`} className={clsx(s.title, s.placeholder)} translate="no">
            {name}
          </h2>
          <p className={clsx(s.meta, 'num')}>{megabytes(upload.size)}</p>
        </div>
        <p className={s.status}>{upload.status === 'failed' ? 'Not added' : 'Getting it ready'}</p>
        {upload.status === 'failed' ? (
          <>
            <div className={s.failure}>
              <WarningCircleIcon size={16} aria-hidden="true" className={s.dangerIcon} />
              <span className={s.failureText}>{upload.error}</span>
            </div>
            <div className={s.actions}>
              <Button variant="text" textSize="small" tone="ink" flush onClick={() => void retryUpload(upload.id)?.then((o) => o.refused && setHint({ kind: 'refused', message: o.refused.message, tone: 'danger' }))}>
                Try again
              </Button>
              <Button variant="text" textSize="small" onClick={() => removeFailedUpload(upload.id)}>
                Remove
              </Button>
            </div>
          </>
        ) : (
          <div className={clsx(s.steps, 'num')}>
            <ProgressBar value={upload.fraction} label={`Uploading ${upload.name}`} className={s.upload} />
            <span className={clsx(s.step, s.stepCurrent)}>Uploading… {sent}%</span>
            <Button variant="text" textSize="small" className={s.inlineAction} onClick={() => cancelUpload(upload.id)}>
              Cancel
            </Button>
          </div>
        )}
      </div>
    </li>
  )
}
