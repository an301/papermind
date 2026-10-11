/**
 * The composer (DESIGN.md §5.20): a quote chip when a selection is attached,
 * an auto-growing textarea, and one button that is Ask, or Stop while an
 * answer streams. Enter sends (never mid-IME), Shift+Enter is a newline,
 * Esc stops, then drops the quote, then returns focus to the PDF.
 */
import { StopIcon, XIcon } from '@phosphor-icons/react'
import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent } from 'react'
import { IconButton } from '../../components/IconButton'
import { count, NBSP } from '../../lib/format'
import { storage } from '../../lib/storage'
import { useReaderStore } from '../shared/readerStore'
import { useComposerPrefill } from './state/composer'
import s from './Composer.module.css'

const LIMIT = 4000
const COUNTER_FROM = 3600
const draftKey = (paperId: string) => `pm:draft:${paperId}`

export interface ComposerProps {
  paperId: string
  streaming: boolean
  onSend: (text: string) => boolean
  onStop: () => void
  /** Text put back after a failed send or "Rephrase". */
  restore?: { seq: number; text: string } | null
}

export function Composer({ paperId, streaming, onSend, onStop, restore }: ComposerProps) {
  const ref = useRef<HTMLTextAreaElement>(null)
  const [text, setText] = useState(() => storage.get(draftKey(paperId)) ?? '')
  const [overLimit, setOverLimit] = useState(false)
  const composing = useRef(false)
  const quote = useReaderStore((st) => st.quote)
  const focusSeq = useReaderStore((st) => st.composerFocusSeq)
  const prefill = useComposerPrefill()

  // Draft per paper, debounced (§5.20).
  useEffect(() => {
    const t = window.setTimeout(() => {
      if (text) storage.set(draftKey(paperId), text)
      else storage.remove(draftKey(paperId))
    }, 300)
    return () => window.clearTimeout(t)
  }, [text, paperId])

  useEffect(() => {
    if (focusSeq) ref.current?.focus()
  }, [focusSeq])

  useEffect(() => {
    if (prefill.seq) {
      setText(prefill.text)
      ref.current?.focus()
    }
  }, [prefill.seq, prefill.text])

  useEffect(() => {
    if (restore?.seq) {
      setText(restore.text)
      ref.current?.focus()
    }
  }, [restore?.seq, restore?.text])

  // Grow from 1 to 8 lines, then scroll inside.
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    el.style.height = 'auto'
    const line = parseFloat(getComputedStyle(el).lineHeight) || 22
    el.style.height = `${Math.min(el.scrollHeight, line * 8 + 8)}px`
  }, [text])

  const send = () => {
    if (streaming) {
      onStop()
      return
    }
    const trimmed = text.trim()
    if (!trimmed) {
      ref.current?.focus()
      return
    }
    if (trimmed.length > LIMIT) {
      setOverLimit(true)
      return
    }
    setOverLimit(false)
    if (onSend(trimmed)) setText('')
  }

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter') {
      if (composing.current || e.nativeEvent.isComposing || e.keyCode === 229) return
      if (e.shiftKey && !(e.metaKey || e.ctrlKey)) return
      e.preventDefault()
      send()
      return
    }
    if (e.key === 'Escape') {
      if (streaming) onStop()
      else if (quote) useReaderStore.getState().setQuote(null)
      else (document.querySelector<HTMLElement>('[data-pdf-scroller]') ?? ref.current)?.focus()
    }
  }

  const locator = quote?.page != null ? `${quote.sectionNumber ? `§${quote.sectionNumber}, ` : ''}p.${NBSP}${quote.page + 1}` : ''

  return (
    <div className={s.dock}>
      <div className={s.composer}>
        {quote ? (
          <div className={s.quoteChip}>
            <span className={s.quoteText}>“{quote.text}”</span>
            {locator ? <span className={s.quoteWhere}>{locator}</span> : null}
            <IconButton label="Remove quote" icon={XIcon} size="xs" onClick={() => useReaderStore.getState().setQuote(null)} />
          </div>
        ) : null}
        <textarea
          ref={ref}
          className={s.input}
          rows={1}
          value={text}
          placeholder={quote ? 'Ask about this passage…' : 'Ask about this paper…'}
          aria-label="Ask about this paper"
          enterKeyHint="send"
          onChange={(e) => {
            setText(e.target.value)
            if (overLimit && e.target.value.length <= LIMIT) setOverLimit(false)
          }}
          onKeyDown={onKeyDown}
          onCompositionStart={() => {
            composing.current = true
          }}
          onCompositionEnd={() => {
            // Safari fires keydown(Enter) right after compositionend; swallow that one.
            window.setTimeout(() => {
              composing.current = false
            }, 0)
          }}
        />
        <div className={s.row}>
          {text.length >= COUNTER_FROM ? (
            <span className={s.counter}>
              {count(text.length)} / {count(LIMIT)}
            </span>
          ) : (
            <span />
          )}
          <button type="button" className={s.send} onClick={send} data-streaming={streaming || undefined}>
            {streaming ? (
              <>
                <StopIcon size={12} weight="fill" aria-hidden="true" /> Stop
              </>
            ) : (
              'Ask'
            )}
          </button>
        </div>
      </div>
      {overLimit ? (
        <p className={s.error} role="alert">
          That’s over the 4,000-character limit. Shorten it or split it into two questions.
        </p>
      ) : null}
    </div>
  )
}
