/**
 * The Reader's 48px top bar (DESIGN.md §5.6): the way back, Contents, the
 * paper's title, the page indicator, zoom, the assistant toggle and More.
 */
import { ArrowLeftIcon, ArrowUpRightIcon, DotsThreeIcon, MinusIcon, PlusIcon, SidebarSimpleIcon } from '@phosphor-icons/react'
import { useState, type KeyboardEvent } from 'react'
import { useNavigate } from 'react-router'
import { useDeletePaper } from '../../api/queries'
import type { PaperMeta } from '../../api/types'
import { ButtonLink } from '../../components/Button'
import { IconButton } from '../../components/IconButton'
import { Menu, MenuItem, MenuSeparator } from '../../components/Menu'
import { Tooltip } from '../../components/Tooltip'
import { openShortcuts } from '../../app/ShortcutsDialog'
import { MOD_KEY } from '../../lib/platform'
import { ZOOM_STEPS, useReaderStore } from '../shared/readerStore'
import s from './TopBar.module.css'

const ZOOM_MENU: { label: string; value: 'fit-width' | 'fit-page' | number }[] = [
  { label: 'Fit width', value: 'fit-width' },
  { label: 'Fit page', value: 'fit-page' },
  { label: 'Actual size', value: 1 },
  ...[0.5, 0.75, 1, 1.25, 1.5, 2].map((z) => ({ label: `${Math.round(z * 100)}%`, value: z })),
]

function PageIndicator({ numPages }: { numPages: number }) {
  const page = useReaderStore((st) => st.currentPage)
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState('')

  if (editing) {
    const commit = () => {
      const n = Number(value)
      if (Number.isInteger(n) && n >= 1 && n <= numPages) {
        useReaderStore.getState().requestScrollToPosition({ page: n - 1, offset: 0 }, 'page')
      }
      setEditing(false)
    }
    const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
      if (e.key === 'Enter') commit()
      if (e.key === 'Escape') setEditing(false)
    }
    return (
      <span className={s.pages}>
        p.{' '}
        <input
          className={s.pageInput}
          autoFocus
          inputMode="numeric"
          aria-label="Go to page"
          value={value}
          onChange={(e) => setValue(e.target.value.replace(/\D/g, ''))}
          onKeyDown={onKeyDown}
          onBlur={() => setEditing(false)}
          onFocus={(e) => e.currentTarget.select()}
        />{' '}
        of {numPages}
      </span>
    )
  }
  return (
    <Tooltip label="Go to page">
      <button
        type="button"
        className={s.pages}
        aria-label={`Page ${page + 1} of ${numPages}`}
        onClick={() => {
          setValue(String(page + 1))
          setEditing(true)
        }}
      >
        p. {page + 1} of {numPages}
      </button>
    </Tooltip>
  )
}

export interface TopBarProps {
  paperId: string
  title: string
  meta: PaperMeta | null
  numPages: number
  onNewConversation: () => void
  outlineToggle: boolean
}

export function TopBar({ paperId, title, meta, numPages, onNewConversation, outlineToggle }: TopBarProps) {
  const outlineOpen = useReaderStore((st) => st.outlineOpen)
  const panelOpen = useReaderStore((st) => st.panelOpen)
  const scale = useReaderStore((st) => st.effectiveScale)
  const store = useReaderStore.getState
  const remove = useDeletePaper()
  const navigate = useNavigate()

  return (
    <header className={s.bar}>
      <ButtonLink to="/" variant="text" className={s.back} icon={<ArrowLeftIcon size={16} aria-hidden="true" />}>
        <span className={s.backLabel}>Your papers</span>
      </ButtonLink>
      {outlineToggle ? (
        <IconButton
          label={outlineOpen ? 'Hide contents' : 'Show contents'}
          shortcut="["
          icon={SidebarSimpleIcon}
          pressed={outlineOpen}
          onClick={() => store().toggleOutline()}
        />
      ) : null}
      <Tooltip label={title} disabled={title.length < 48}>
        <h1 className={s.title} tabIndex={title.length < 48 ? undefined : 0}>
          {title}
        </h1>
      </Tooltip>

      <div className={s.right}>
        {numPages ? <PageIndicator numPages={numPages} /> : null}
        <div className={s.zoom}>
          <IconButton label="Zoom out" shortcut={`${MOD_KEY} −`} icon={MinusIcon} onClick={() => store().zoomOut()} disabled={scale <= ZOOM_STEPS[0] + 0.001} />
          <Menu
            align="center"
            trigger={
              <button type="button" className={s.zoomValue} aria-label={`Zoom ${Math.round(scale * 100)}%`}>
                {Math.round(scale * 100)}%
              </button>
            }
          >
            {ZOOM_MENU.map((z) => (
              <MenuItem key={z.label} onSelect={() => store().setZoom(z.value)}>
                {z.label}
              </MenuItem>
            ))}
          </Menu>
          <IconButton label="Zoom in" shortcut={`${MOD_KEY} +`} icon={PlusIcon} onClick={() => store().zoomIn()} />
        </div>
        <IconButton
          label={panelOpen ? 'Hide assistant' : 'Show assistant'}
          shortcut="]"
          icon={SidebarSimpleIcon}
          mirrored
          pressed={panelOpen}
          onClick={() => store().togglePanel()}
        />
        <Menu trigger={<IconButton label="More" icon={DotsThreeIcon} />}>
          <MenuItem onSelect={onNewConversation}>New conversation</MenuItem>
          <MenuSeparator />
          <MenuItem onSelect={() => openShortcuts()} shortcut="?">
            Keyboard shortcuts
          </MenuItem>
          {meta?.arxiv_id ? (
            <MenuItem icon={ArrowUpRightIcon} onSelect={() => window.open(`https://arxiv.org/abs/${meta.arxiv_id}`, '_blank', 'noopener,noreferrer')}>
              Paper on arXiv
            </MenuItem>
          ) : null}
          <MenuSeparator />
          <MenuItem
            danger
            onSelect={() => {
              remove.mutate(paperId, { onSuccess: () => navigate('/') })
            }}
          >
            Remove from your papers
          </MenuItem>
        </Menu>
      </div>
    </header>
  )
}
