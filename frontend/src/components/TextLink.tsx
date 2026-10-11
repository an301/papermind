import type { ButtonHTMLAttributes, Ref } from 'react'
import { Link, type LinkProps } from 'react-router'
import { clsx } from '../lib/clsx'
import s from './TextLink.module.css'

export const textLinkClass = (quiet = false): string => clsx(s.link, quiet && s.quiet)

/** An in-app link in running text. */
export function TextLink({ quiet, className, ...rest }: LinkProps & { quiet?: boolean }) {
  return <Link className={clsx(textLinkClass(quiet), className)} {...rest} />
}

/** A button that reads as a link inside a sentence (concept names in the profile and callouts). */
export function LinkButton({
  quiet,
  className,
  type = 'button',
  ref,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { quiet?: boolean; ref?: Ref<HTMLButtonElement> }) {
  return <button ref={ref} type={type} className={clsx(textLinkClass(quiet), className)} {...rest} />
}
