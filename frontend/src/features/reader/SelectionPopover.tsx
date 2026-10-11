/**
 * The selection toolbar (DESIGN.md §5.12): Explain sends "Explain this
 * passage." with the selection as the quote; Ask about this attaches it to the
 * composer. It appears and disappears instantly (selecting is high-frequency)
 * and never takes focus, so the selection survives.
 */
import { DotsThreeIcon } from '@phosphor-icons/react'
import { Popover } from 'radix-ui'
import { useMemo } from 'react'
import { Kbd } from '../../components/Kbd'
import { Menu, MenuItem } from '../../components/Menu'
import { announce } from '../../lib/announce'
import { copyText } from '../../lib/clipboard'
import { authorsShort, NBSP, yearOf } from '../../lib/format'
import { usePreferences } from '../../lib/preferences'
import { useReaderStore } from '../shared/readerStore'
import type { ReadSelection } from './pdf/selection'
import s from './SelectionPopover.module.css'

export function explainSelection() {
  const store = useReaderStore.getState()
  const sel = store.selection
  if (!sel) return
  store.requestAsk('Explain this passage.', { text: sel.text, page: sel.page, sectionNumber: sel.sectionNumber ?? null })
  store.setSelection(null)
  document.getSelection()?.removeAllRanges()
}

export function askAboutSelection() {
  useReaderStore.getState().attachSelectionAsQuote()
  document.getSelection()?.removeAllRanges()
}

export function SelectionPopover({ selection }: { selection: ReadSelection | null }) {
  const meta = useReaderStore((st) => st.meta)
  const seen = usePreferences((st) => st.seen['selection-tip'] ?? 0)

  const anchor = useMemo(() => {
    if (!selection) return null
    const { first, bounds } = selection
    return {
      current: {
        getBoundingClientRect: () => DOMRect.fromRect({ x: bounds.left, y: first.top, width: bounds.width, height: first.height }),
      },
    }
  }, [selection])

  if (!selection || !anchor) return null

  const citation = () => {
    const who = meta ? authorsShort(meta.authors, yearOf(meta.published)) : ''
    const section = useReaderStore.getState().selection?.sectionNumber
    const where = [who, section ? `§${section}` : null, `p.${NBSP}${selection.page + 1}`].filter(Boolean).join(', ')
    return `“${selection.text}” (${where})`
  }

  return (
    <Popover.Root open>
      <Popover.Anchor virtualRef={anchor} />
      <Popover.Portal>
        <Popover.Content
          className={s.popover}
          side="top"
          sideOffset={8}
          collisionPadding={16}
          onOpenAutoFocus={(e) => {
            e.preventDefault()
            if (seen < 2) usePreferences.getState().bumpSeen('selection-tip')
          }}
          onCloseAutoFocus={(e) => e.preventDefault()}
        >
          <div role="toolbar" aria-label="Selection" className={s.toolbar}>
            <button type="button" className={s.action} onMouseDown={(e) => e.preventDefault()} onClick={explainSelection}>
              Explain <Kbd>E</Kbd>
            </button>
            <button type="button" className={s.action} onMouseDown={(e) => e.preventDefault()} onClick={askAboutSelection}>
              Ask about this <Kbd>A</Kbd>
            </button>
            <span className={s.divider} aria-hidden="true" />
            <Menu
              trigger={
                <button type="button" className={s.more} aria-label="More">
                  <DotsThreeIcon size={16} aria-hidden="true" />
                </button>
              }
            >
              <MenuItem
                onSelect={async () => {
                  if (await copyText(selection.text)) announce('Copied')
                }}
              >
                Copy
              </MenuItem>
              <MenuItem
                onSelect={async () => {
                  if (await copyText(citation())) announce('Copied')
                }}
              >
                Copy with citation
              </MenuItem>
            </Menu>
          </div>
          {seen < 2 ? <p className={s.tip}>Tip: press E to explain a selection.</p> : null}
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  )
}
