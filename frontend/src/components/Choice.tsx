import { CheckIcon } from '@phosphor-icons/react'
import type { InputHTMLAttributes, ReactNode, Ref } from 'react'
import { clsx } from '../lib/clsx'
import s from './Choice.module.css'

interface ChoiceProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'type'> {
  label: ReactNode
  /** Second line in Sofia 13 --ink-2. */
  description?: ReactNode
  /** Label in 14/550 (onboarding options) instead of 14/400. */
  strong?: boolean
  rowClassName?: string
  ref?: Ref<HTMLInputElement>
}

/** A native checkbox with its label as one hit area. */
export function Checkbox({ label, description, strong, rowClassName, className, disabled, ref, ...rest }: ChoiceProps) {
  return (
    <label className={clsx(s.row, rowClassName)} data-disabled={disabled || undefined}>
      <span className={s.box}>
        <input ref={ref} type="checkbox" className={clsx(s.input, className)} disabled={disabled} {...rest} />
        <CheckIcon className={s.check} size={12} weight="bold" aria-hidden="true" />
      </span>
      <span className={s.text}>
        <span className={clsx(strong && s.labelStrong)}>{label}</span>
        {description ? <span className={s.description}>{description}</span> : null}
      </span>
    </label>
  )
}

/** A native radio with its label as one hit area. Group them in a <fieldset> with a <legend>. */
export function Radio({ label, description, strong, rowClassName, className, disabled, ref, ...rest }: ChoiceProps) {
  return (
    <label className={clsx(s.row, rowClassName)} data-disabled={disabled || undefined}>
      <span className={s.box}>
        <input ref={ref} type="radio" className={clsx(s.input, s.radio, className)} disabled={disabled} {...rest} />
      </span>
      <span className={s.text}>
        <span className={clsx(strong && s.labelStrong)}>{label}</span>
        {description ? <span className={s.description}>{description}</span> : null}
      </span>
    </label>
  )
}
