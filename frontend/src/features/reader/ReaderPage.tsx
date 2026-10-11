/**
 * The Reader (DESIGN.md §4.4): the paper in the middle, its contents on the
 * left, the assistant as its outer margin on the right. Three columns, no
 * boxes, separated by tone steps. Shared state with the assistant panel goes
 * through the reader store (src/features/shared/README.md).
 */
import { ArrowUUpLeftIcon } from '@phosphor-icons/react'
import { useCallback, useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent } from 'react'
import { useParams, useSearchParams } from 'react-router'
import { isNotFound, pdfUrl } from '../../api/client'
import { usePaper, useUpdateSession, useUser } from '../../api/queries'
import type { Level } from '../../api/types'
import { Button, ButtonLink } from '../../components/Button'
import { ErrorState } from '../../components/ErrorState'
import { ProgressBar } from '../../components/ProgressBar'
import { clsx } from '../../lib/clsx'
import { isTypingTarget } from '../../lib/platform'
import { usePreferences } from '../../lib/preferences'
import { recordOpen, recordPosition } from '../../lib/readingProgress'
import { AssistantPanel } from '../assistant'
import { useConversation } from '../assistant/state/conversation'
import { displayTitle, isFailed } from '../library/paperText'
import { clampPanelWidth, PANEL_DEFAULT, PANEL_MAX, PANEL_MIN, sectionAtPage, useReaderStore } from '../shared/readerStore'
import { Outline } from './Outline'
import { PdfViewer } from './pdf/PdfViewer'
import type { ReadSelection } from './pdf/selection'
import { askAboutSelection, explainSelection, SelectionPopover } from './SelectionPopover'
import { TopBar } from './TopBar'
import s from './ReaderPage.module.css'

const LEVELS: readonly Level[] = ['beginner', 'intermediate', 'expert']

function useMedia(query: string): boolean {
  const [matches, setMatches] = useState(() => window.matchMedia(query).matches)
  useEffect(() => {
    const mq = window.matchMedia(query)
    const on = () => setMatches(mq.matches)
    mq.addEventListener('change', on)
    return () => mq.removeEventListener('change', on)
  }, [query])
  return matches
}

/** The resize separator on the panel's left edge (§4.4 "Panel resize"). */
function Separator() {
  const width = useReaderStore((st) => st.panelWidth)
  const [dragging, setDragging] = useState(false)
  const start = useRef({ x: 0, width })

  const onPointerDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    e.currentTarget.setPointerCapture(e.pointerId)
    start.current = { x: e.clientX, width: useReaderStore.getState().panelWidth }
    setDragging(true)
  }
  const onPointerMove = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (!dragging) return
    useReaderStore.getState().setPanelWidth(start.current.width + (start.current.x - e.clientX))
  }
  const onKeyDown = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    const step = e.shiftKey ? 64 : 16
    const store = useReaderStore.getState()
    if (e.key === 'ArrowLeft') store.setPanelWidth(store.panelWidth + step)
    else if (e.key === 'ArrowRight') store.setPanelWidth(store.panelWidth - step)
    else if (e.key === 'Home') store.setPanelWidth(PANEL_MIN)
    else if (e.key === 'End') store.setPanelWidth(PANEL_MAX)
    else return
    e.preventDefault()
  }
  return (
    <div
      className={s.separator}
      data-dragging={dragging || undefined}
      role="separator"
      aria-orientation="vertical"
      aria-label="Resize assistant"
      aria-valuemin={PANEL_MIN}
      aria-valuemax={PANEL_MAX}
      aria-valuenow={width}
      tabIndex={0}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={() => setDragging(false)}
      onKeyDown={onKeyDown}
      onDoubleClick={() => useReaderStore.getState().setPanelWidth(PANEL_DEFAULT)}
    />
  )
}

/** "Back to p. 11" after a jump that moved more than a screen; 10 s, or Alt+← (§5.8). */
function BackPill() {
  const history = useReaderStore((st) => st.jumpHistory)
  const [visible, setVisible] = useState(false)
  const last = history[history.length - 1]
  useEffect(() => {
    if (!history.length) {
      setVisible(false)
      return
    }
    setVisible(true)
    const t = window.setTimeout(() => setVisible(false), 10_000)
    return () => window.clearTimeout(t)
  }, [history.length])
  if (!visible || !last) return null
  return (
    <button
      type="button"
      className={s.backPill}
      onClick={() => {
        useReaderStore.getState().jumpBack()
        setVisible(false)
      }}
    >
      <ArrowUUpLeftIcon size={16} aria-hidden="true" /> Back to p.&nbsp;{last.page + 1}
    </button>
  )
}

export function ReaderPage() {
  const { paperId = '' } = useParams()
  const [params, setParams] = useSearchParams()
  const paper = usePaper(paperId)
  const summary = paper.data?.summary
  const meta = paper.data?.meta ?? null
  const title = summary ? displayTitle(summary) : ''
  const user = useUser()
  const updateSession = useUpdateSession()
  const wide = useMedia('(min-width: 1440px)')
  const docked = useMedia('(min-width: 1000px)')

  const panelOpen = useReaderStore((st) => st.panelOpen)
  const panelWidth = useReaderStore((st) => st.panelWidth)
  const outlineOpen = useReaderStore((st) => st.outlineOpen)
  const level = useReaderStore((st) => st.level)
  const session = useReaderStore((st) => st.session)
  const page = useReaderStore((st) => st.currentPage)
  const offset = useReaderStore((st) => st.currentOffset)
  const singleKeys = usePreferences((st) => st.singleKeyShortcuts)

  const [selection, setSelection] = useState<ReadSelection | null>(null)
  const [pdfError, setPdfError] = useState<string | null>(null)
  const [reloadKey, setReloadKey] = useState(0)
  const [numPages, setNumPages] = useState(0)

  // ---- open the paper in the shared store; depth from ?depth= or the profile
  useEffect(() => {
    const fromUrl = params.get('depth') as Level | null
    const initial = fromUrl && LEVELS.includes(fromUrl) ? fromUrl : user.data?.level
    useReaderStore.getState().openPaper(paperId, initial ? { level: initial } : {})
    useReaderStore.getState().setOutlineOpen(window.matchMedia('(min-width: 1440px)').matches && params.get('outline') !== '0')
    // Below the docked width the panel is an overlay: start with the paper, open the assistant on demand.
    if (!window.matchMedia('(min-width: 1000px)').matches) useReaderStore.getState().setPanelOpen(false)
    return () => useReaderStore.getState().closePaper()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [paperId])

  useEffect(() => {
    useReaderStore.getState().setMeta(meta)
  }, [meta])

  useEffect(() => {
    if (summary?.status === 'ready') recordOpen(paperId)
  }, [paperId, summary?.status])

  useEffect(() => {
    if (title) document.title = `${title} · PaperMind`
  }, [title])

  // Mirror depth to the URL so a link carries it (§5.9).
  useEffect(() => {
    if (params.get('depth') === level) return
    const next = new URLSearchParams(params)
    next.set('depth', level)
    setParams(next, { replace: true })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [level])

  // ---- restore the reading position once the session arrives, then save it every 2 s while reading
  const restored = useRef<string | null>(null)
  useEffect(() => {
    if (!session || restored.current === session.session_id) return
    restored.current = session.session_id
    const fromUrl = Number(params.get('p'))
    const target = Number.isInteger(fromUrl) && fromUrl > 0 ? { page: fromUrl - 1, offset: 0 } : session.scroll
    if (target.page > 0 || target.offset > 0) {
      useReaderStore.getState().requestScrollToPosition({ page: target.page, offset: target.offset }, 'restore', 'instant')
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session])

  useEffect(() => {
    if (!session) return
    const t = window.setTimeout(() => {
      updateSession.mutate({ sessionId: session.session_id, scroll: { page, offset, zoom: null } })
      recordPosition(paperId, { page, section: sectionAtPage(meta, page)?.number ?? null })
    }, 2000)
    return () => window.clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, offset, session?.session_id])

  // ---- keyboard (§8.2): E, A with a selection; [ and ]; /; Alt+← back; ⌘± zoom
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const store = useReaderStore.getState()
      if ((e.metaKey || e.ctrlKey) && (e.key === '=' || e.key === '+')) {
        e.preventDefault()
        store.zoomIn()
        return
      }
      if ((e.metaKey || e.ctrlKey) && e.key === '-') {
        e.preventDefault()
        store.zoomOut()
        return
      }
      if (e.altKey && e.key === 'ArrowLeft') {
        if (store.jumpBack()) e.preventDefault()
        return
      }
      if (e.metaKey || e.ctrlKey || e.altKey || isTypingTarget(e.target) || !singleKeys) return
      if (store.selection && (e.key === 'e' || e.key === 'E')) {
        e.preventDefault()
        explainSelection()
        setSelection(null)
      } else if (store.selection && (e.key === 'a' || e.key === 'A')) {
        e.preventDefault()
        askAboutSelection()
        setSelection(null)
      } else if (e.key === ']') store.togglePanel()
      else if (e.key === '[') store.toggleOutline()
      else if (e.key === '/') {
        e.preventDefault()
        store.requestComposerFocus()
      } else if (e.key === 'Escape' && store.selection) {
        document.getSelection()?.removeAllRanges()
        store.setSelection(null)
        setSelection(null)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [singleKeys])

  // Dismiss the popover on a scroll of more than a few px (§5.12).
  useEffect(() => {
    if (!selection) return
    const onScroll = () => setSelection(null)
    const el = document.querySelector('[data-pdf-scroller]')
    el?.addEventListener('scroll', onScroll, { once: true, passive: true })
    return () => el?.removeEventListener('scroll', onScroll)
  }, [selection])

  const onSelection = useCallback((sel: ReadSelection | null) => setSelection(sel), [])

  const newConversation = () => {
    const userId = user.data?.user_id
    if (userId) void useConversation.getState().restart(userId, level)
  }

  // ---- states before there's a PDF to show
  if (paper.isError && isNotFound(paper.error)) {
    return (
      <div className={s.reader}>
        <TopBar paperId={paperId} title="" meta={null} numPages={0} onNewConversation={newConversation} outlineToggle={false} />
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
      </div>
    )
  }

  const pdfReady = !!summary && summary.status !== 'queued' && !(summary.status === 'processing' && summary.stage === 'downloading')
  const outlineAsColumn = wide && outlineOpen && !!meta
  const outlineAsDrawer = !wide && outlineOpen && !!meta
  const panelWidthPx = docked ? clampPanelWidth(Math.min(panelWidth, Math.max(PANEL_MIN, window.innerWidth - 620))) : 0

  return (
    <div className={s.reader}>
      <TopBar paperId={paperId} title={title} meta={meta} numPages={numPages || meta?.num_pages || 0} onNewConversation={newConversation} outlineToggle={!!meta} />
      <div
        className={s.body}
        style={{
          gridTemplateColumns: `${outlineAsColumn ? 'var(--outline-w) ' : ''}minmax(0, 1fr)${docked ? (panelOpen ? ` ${panelWidthPx}px` : ' var(--rail-w)') : ''}`,
        }}
      >
        {outlineAsColumn ? (
          <div className={s.outlineColumn}>
            <Outline meta={meta!} />
          </div>
        ) : null}

        <main className={s.desk}>
          {outlineAsDrawer ? (
            <div className={s.drawer}>
              <Outline meta={meta!} onNavigate={() => useReaderStore.getState().setOutlineOpen(false)} />
            </div>
          ) : null}
          {pdfError ? (
            <div className={s.message}>
              <ErrorState
                action={
                  <Button
                    size="sm"
                    onClick={() => {
                      setPdfError(null)
                      setReloadKey((k) => k + 1)
                    }}
                  >
                    Try again
                  </Button>
                }
              >
                Couldn’t load the PDF. The server didn’t send it.
              </ErrorState>
            </div>
          ) : summary && isFailed(summary) ? (
            <div className={s.message}>
              <ErrorState action={<ButtonLink to="/" variant="secondary">Back to your papers</ButtonLink>}>
                Couldn’t finish preparing this paper{summary.error ? `: ${summary.error}` : ''}.
              </ErrorState>
            </div>
          ) : pdfReady ? (
            <PdfViewer url={pdfUrl(paperId)} onSelection={onSelection} onLoaded={setNumPages} onError={setPdfError} reloadKey={reloadKey} />
          ) : (
            <div className={s.message}>
              <p className={s.preparing}>Getting this paper from arXiv…</p>
              <ProgressBar value={summary?.progress ?? 0} label="Preparing the paper" />
            </div>
          )}
          <BackPill />
        </main>

        {docked ? (
          panelOpen ? (
            <aside className={s.panel} aria-label="Assistant">
              <Separator />
              <AssistantPanel />
            </aside>
          ) : (
            <aside className={s.rail} aria-label="Assistant">
              <button type="button" className={s.railButton} onClick={() => useReaderStore.getState().setPanelOpen(true)}>
                <span className={s.visuallyHidden}>Show assistant</span>
                <span aria-hidden="true">/</span>
              </button>
            </aside>
          )
        ) : panelOpen ? (
          <aside className={clsx(s.panel, s.overlay)} aria-label="Assistant">
            <AssistantPanel />
          </aside>
        ) : null}
      </div>
      <SelectionPopover selection={selection} />
    </div>
  )
}

export default ReaderPage
