import type { ButtonHTMLAttributes, ReactNode, Ref } from 'react'
import { Link, type LinkProps } from 'react-router'
import { clsx } from '../lib/clsx'
import { useDelayedFlag } from '../lib/useDelayedFlag'
import s from './Button.module.css'
import { Spinner } from './Spinner'

export type ButtonVariant = 'primary' | 'secondary' | 'text' | 'danger'
export type ButtonSize = 'sm' | 'md' | 'lg'

interface StyleProps {
  variant?: ButtonVariant
  size?: ButtonSize
  /** Text buttons only: 'small' = Sofia 13/500, the default is 14/550. */
  textSize?: 'small' | 'default'
  /** Text buttons only: ink or danger instead of --ink-2. */
  tone?: 'default' | 'ink' | 'danger'
  block?: boolean
  /** Text buttons only: hang the padding into the margin so the label aligns with the text above. */
  flush?: boolean
}

export function buttonClass({ variant = 'secondary', size = 'md', textSize = 'default', tone = 'default', block, flush }: StyleProps = {}): string {
  return clsx(
    s.button,
    s[variant],
    size === 'lg' && s.lg,
    size === 'sm' && s.sm,
    variant === 'text' && textSize === 'small' && s.small,
    variant === 'text' && tone === 'ink' && s.textInk,
    variant === 'text' && tone === 'danger' && s.textDanger,
    block && s.block,
    flush && s.flush,
  )
}

export interface ButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'children'>, StyleProps {
  children: ReactNode
  /**
   * Busy: keeps the button where it is and swaps the label for `busyLabel`
   * ("Adding…"); a 12px spinner joins it after 200ms (DESIGN.md §5.1, §5.27).
   */
  busy?: boolean
  busyLabel?: ReactNode
  /** Leading icon (a Phosphor icon element, already aria-hidden). */
  icon?: ReactNode
  ref?: Ref<HTMLButtonElement>
}

/**
 * Buttons are never pre-disabled (§5): pass `disabled` only for controls that
 * truly can't act, and never put a tooltip on one.
 */
export function Button({
  variant = 'secondary',
  size = 'md',
  textSize,
  tone,
  block,
  flush,
  busy = false,
  busyLabel,
  icon,
  className,
  children,
  type = 'button',
  disabled,
  ref,
  onClick,
  ...rest
}: ButtonProps) {
  const showSpinner = useDelayedFlag(busy, 200, 0)
  return (
    <button
      ref={ref}
      type={type}
      className={clsx(buttonClass({ variant, size, textSize, tone, block, flush }), className)}
      aria-disabled={disabled || undefined}
      aria-busy={busy || undefined}
      onClick={disabled ? (e) => e.preventDefault() : onClick}
      {...rest}
    >
      {busy && showSpinner ? <Spinner /> : icon}
      <span className={s.label}>{busy && busyLabel ? busyLabel : children}</span>
    </button>
  )
}

export interface ButtonLinkProps extends LinkProps, StyleProps {
  icon?: ReactNode
}

/** A navigation that looks like a button ("Back to your papers"). Navigation stays a real link. */
export function ButtonLink({ variant, size, textSize, tone, block, flush, icon, className, children, ...rest }: ButtonLinkProps) {
  return (
    <Link className={clsx(buttonClass({ variant, size, textSize, tone, block, flush }), className)} {...rest}>
      {icon}
      <span className={s.label}>{children}</span>
    </Link>
  )
}
