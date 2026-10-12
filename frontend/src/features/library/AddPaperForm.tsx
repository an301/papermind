import { WarningCircleIcon } from '@phosphor-icons/react'
import { useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { Link } from 'react-router'
import { Button } from '../../components/Button'
import { Input } from '../../components/TextField'
import { textLinkClass } from '../../components/TextLink'
import { clsx } from '../../lib/clsx'
import { NBSP } from '../../lib/format'
import { parseArxiv } from '../../lib/arxiv'
import { useCoarsePointer } from '../../lib/useCoarsePointer'
import { addArxiv, addFiles, MAX_UPLOAD_MB, setHint, useAddHint, validateArxiv, type Hint } from './addFlow'
import s from './AddPaperForm.module.css'

export const ADD_FIELD_ID = 'add-paper'

/**
 * "The field is the empty state" (DESIGN.md §5.4): one field that takes an
 * arXiv id or link, a Choose PDF button inside it, and the hint line under it
 * where every confirmation and problem appears.
 *
 * Validation runs on paste, Enter and blur, never per keystroke, until the
 * field is in error; then it re-checks on every keystroke so the error clears
 * the moment the input is fixed.
 */
export function AddPaperForm() {
  const hint = useAddHint((st) => st.hint)
  const [value, setValue] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const hintId = useId()
  // Touch screens can't drop a file on the page, and the long sentence doesn't fit a phone field.
  const coarse = useCoarsePointer()
  const busy = hint.kind === 'adding'
  const invalid = hint.kind === 'invalid'

  // Leaving the Library and coming back starts from a clean hint.
  useEffect(() => () => setHint({ kind: 'idle' }), [])

  async function submit(e?: FormEvent) {
    e?.preventDefault()
    if (busy) return
    if (!value.trim()) {
      inputRef.current?.focus()
      return
    }
    const outcome = await addArxiv(value)
    if (outcome === 'added') setValue('')
    inputRef.current?.focus()
  }

  return (
    <form className={s.form} onSubmit={submit} noValidate>
      <div className={s.row}>
        <Input
          ref={inputRef}
          id={ADD_FIELD_ID}
          size="lg"
          type="text"
          inputMode="url"
          spellCheck={false}
          autoCapitalize="off"
          autoComplete="off"
          autoCorrect="off"
          enterKeyHint="go"
          aria-label="arXiv link or ID"
          aria-describedby={hintId}
          placeholder={coarse ? 'Paste an arXiv link or ID…' : 'Paste an arXiv link or ID, or drop a PDF on this page…'}
          value={value}
          invalid={invalid}
          controlClassName={s.control}
          onChange={(e) => {
            const next = e.target.value
            setValue(next)
            if (invalid || hint.kind === 'ready' || hint.kind === 'exists' || hint.kind === 'refused') {
              if (!next.trim()) setHint({ kind: 'idle' })
              else if (invalid) {
                const ref = parseArxiv(next)
                if (ref) setHint({ kind: 'ready', ref })
              } else setHint({ kind: 'idle' })
            }
          }}
          onPaste={() => {
            // Read the field after the paste lands (it may have replaced a selection).
            window.setTimeout(() => {
              const ref = validateArxiv(inputRef.current?.value ?? '')
              if (ref) setHint({ kind: 'ready', ref })
            }, 0)
          }}
          onBlur={() => {
            if (!value.trim() || busy) return
            // Leaving the field to press a button in the hint line ("Try again",
            // "Add v5") must not replace that hint before the click lands.
            if (hint.kind !== 'idle' && hint.kind !== 'invalid') return
            const ref = validateArxiv(value)
            if (ref) setHint({ kind: 'ready', ref })
          }}
          trailing={
            <Button variant="text" textSize="small" className={s.choose} onClick={() => fileRef.current?.click()}>
              Choose PDF
            </Button>
          }
        />
        <Button type="submit" variant="primary" size="lg" busy={busy} busyLabel="Adding…" className={s.submit}>
          Add paper
        </Button>
        <input
          ref={fileRef}
          type="file"
          accept="application/pdf,.pdf"
          multiple
          hidden
          tabIndex={-1}
          onChange={(e) => {
            const files = Array.from(e.target.files ?? [])
            e.target.value = ''
            void addFiles(files)
          }}
        />
      </div>
      <HintLine id={hintId} hint={hint} />
    </form>
  )
}

function HintLine({ id, hint }: { id: string; hint: Hint }) {
  let content: ReactNode
  let tone: 'hint' | 'status' | 'error' = 'hint'
  let icon: 'danger' | 'neutral' | null = null
  switch (hint.kind) {
    case 'idle':
      content = `PDF up to ${MAX_UPLOAD_MB}${NBSP}MB. arXiv IDs look like 1706.03762.`
      break
    case 'invalid':
      tone = 'error'
      icon = 'neutral'
      content = hint.message
      break
    case 'ready':
      tone = 'status'
      content = `Press Enter to add arXiv${NBSP}${hint.ref.display}.`
      break
    case 'adding':
      tone = 'status'
      content = `Adding ${hint.ref.display} from arXiv…`
      break
    case 'exists':
      tone = 'status'
      content = (
        <>
          <cite className={s.cite} translate="no">
            {hint.title}
          </cite>{' '}
          is already in your papers.{' '}
          <Link to={`/paper/${encodeURIComponent(hint.paperId)}`} className={textLinkClass()}>
            Open it
          </Link>
        </>
      )
      break
    case 'refused':
      tone = 'error'
      icon = hint.tone === 'danger' ? 'danger' : 'neutral'
      content = (
        <>
          {hint.message}
          {hint.actions?.map((action) => (
            <span key={action.label}>
              {' '}
              {action.to ? (
                <Link to={action.to} className={textLinkClass()}>
                  {action.label}
                </Link>
              ) : (
                <button type="button" className={textLinkClass()} onClick={action.run}>
                  {action.label}
                </button>
              )}
            </span>
          ))}
          {hint.retry ? (
            <>
              {' '}
              <button type="button" className={textLinkClass()} onClick={hint.retry}>
                Try again
              </button>
            </>
          ) : null}
        </>
      )
      break
  }
  return (
    <p id={id} className={clsx(s.hint, tone !== 'hint' && s.hintInk)}>
      {icon ? <WarningCircleIcon size={16} aria-hidden="true" className={clsx(s.hintIcon, icon === 'danger' && s.danger)} /> : null}
      <span>{content}</span>
    </p>
  )
}
