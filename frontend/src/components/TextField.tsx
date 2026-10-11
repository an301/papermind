import type { Icon } from '@phosphor-icons/react'
import { useId, type InputHTMLAttributes, type ReactNode, type Ref } from 'react'
import { clsx } from '../lib/clsx'
import s from './TextField.module.css'
import { VisuallyHidden } from './VisuallyHidden'

export interface InputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'size'> {
  size?: 'md' | 'lg'
  leadingIcon?: Icon
  /** Inside the field, at the right: a text button ("Choose PDF") or a clear button. */
  trailing?: ReactNode
  invalid?: boolean
  controlClassName?: string
  ref?: Ref<HTMLInputElement>
}

/** The bare styled input. Prefer TextField, which wires the label and message. */
export function Input({ size = 'md', leadingIcon: Lead, trailing, invalid, className, controlClassName, ref, ...rest }: InputProps) {
  return (
    <div
      className={clsx(s.control, size === 'lg' && s.lg, Lead && s.hasLeading, controlClassName)}
      data-invalid={invalid || undefined}
    >
      {Lead ? (
        <span className={s.leading}>
          <Lead size={16} aria-hidden="true" />
        </span>
      ) : null}
      <input ref={ref} className={clsx(s.input, className)} aria-invalid={invalid || undefined} {...rest} />
      {trailing ? <div className={s.trailing}>{trailing}</div> : null}
    </div>
  )
}

export type MessageTone = 'hint' | 'error' | 'status'

export interface FieldMessageProps {
  id?: string
  tone?: MessageTone
  /** Status icon for errors: WarningCircle in --danger when work was lost or refused (§2.6). */
  icon?: Icon
  iconTone?: 'danger' | 'warn' | 'neutral' | 'success'
  children: ReactNode
  className?: string
}

/** The line under a field: hint, confirmation or error (§5.25: errors sit where the thing would have been). */
export function FieldMessage({ id, tone = 'hint', icon: IconCmp, iconTone = 'neutral', children, className }: FieldMessageProps) {
  return (
    <div
      id={id}
      className={clsx(s.message, tone === 'error' && s.messageError, tone === 'status' && s.messageStatus, className)}
    >
      {IconCmp ? (
        <IconCmp
          size={16}
          aria-hidden="true"
          className={clsx(s.messageIcon, iconTone === 'danger' && s.danger, iconTone === 'warn' && s.warn)}
        />
      ) : null}
      <span>{children}</span>
    </div>
  )
}

export interface TextFieldProps extends InputProps {
  label: string
  /** Keep the label for screen readers only (the placeholder and context carry it visually). */
  hideLabel?: boolean
  hint?: ReactNode
  error?: ReactNode
}

/** Label + input + hint or error, wired with aria-describedby. */
export function TextField({ label, hideLabel, hint, error, id, className, ...inputProps }: TextFieldProps) {
  const autoId = useId()
  const inputId = id ?? `f${autoId}`
  const messageId = `${inputId}-message`
  const hasMessage = !!(error || hint)
  return (
    <div className={clsx(s.field, className)}>
      {hideLabel ? (
        <VisuallyHidden>
          <label htmlFor={inputId}>{label}</label>
        </VisuallyHidden>
      ) : (
        <label htmlFor={inputId} className={s.label}>
          {label}
        </label>
      )}
      <Input id={inputId} invalid={!!error} aria-describedby={hasMessage ? messageId : undefined} {...inputProps} />
      {error ? (
        <FieldMessage id={messageId} tone="error">
          {error}
        </FieldMessage>
      ) : hint ? (
        <FieldMessage id={messageId}>{hint}</FieldMessage>
      ) : null}
    </div>
  )
}
