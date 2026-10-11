import type { Icon } from '@phosphor-icons/react'
import type { ButtonHTMLAttributes, Ref } from 'react'
import { Link } from 'react-router'
import { clsx } from '../lib/clsx'
import s from './IconButton.module.css'
import { Tooltip } from './Tooltip'

interface CommonProps {
  /** Accessible name, also the tooltip text (DESIGN.md §3 rule 6). */
  label: string
  icon: Icon
  /** Shortcut shown in the tooltip, in platform text. */
  shortcut?: string
  size?: 'xs' | 'sm' | 'md'
  /** Mirror the icon (the assistant toggle uses SidebarSimple mirrored). */
  mirrored?: boolean
  tooltipSide?: 'top' | 'right' | 'bottom' | 'left'
  /** Skip the tooltip (only when a visible label sits right beside the button). */
  noTooltip?: boolean
  className?: string
}

export interface IconButtonProps extends CommonProps, Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'children' | 'aria-label'> {
  /** Toggle buttons: aria-pressed, --selected background and the `fill` weight when on. */
  pressed?: boolean
  ref?: Ref<HTMLButtonElement>
}

/** An icon-only button with its tooltip. The icon is aria-hidden; the label names the button. */
export function IconButton({
  label,
  icon: IconCmp,
  shortcut,
  size = 'sm',
  mirrored,
  pressed,
  tooltipSide,
  noTooltip,
  className,
  type = 'button',
  ref,
  ...rest
}: IconButtonProps) {
  const button = (
    <button
      ref={ref}
      type={type}
      aria-label={label}
      aria-pressed={pressed}
      className={clsx(s.iconButton, size === 'md' && s.md, size === 'xs' && s.xs, className)}
      {...rest}
    >
      <IconCmp size={16} weight={pressed ? 'fill' : 'regular'} mirrored={mirrored} aria-hidden="true" />
    </button>
  )
  return (
    <Tooltip label={label} shortcut={shortcut} side={tooltipSide} disabled={noTooltip}>
      {button}
    </Tooltip>
  )
}

export interface IconLinkProps extends CommonProps {
  to: string
  /** Mark as the current page (aria-current) and show the selected state. */
  current?: boolean
}

/** An icon-only navigation (the Settings gear). Still a real link. */
export function IconLink({ label, icon: IconCmp, shortcut, size = 'sm', mirrored, tooltipSide, noTooltip, className, to, current }: IconLinkProps) {
  return (
    <Tooltip label={label} shortcut={shortcut} side={tooltipSide} disabled={noTooltip}>
      <Link
        to={to}
        aria-label={label}
        aria-current={current ? 'page' : undefined}
        className={clsx(s.iconButton, size === 'md' && s.md, size === 'xs' && s.xs, className)}
        data-current={current || undefined}
      >
        <IconCmp size={16} weight={current ? 'fill' : 'regular'} mirrored={mirrored} aria-hidden="true" />
      </Link>
    </Tooltip>
  )
}
