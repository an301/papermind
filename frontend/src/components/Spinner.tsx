import { clsx } from '../lib/clsx'
import s from './Spinner.module.css'

/**
 * The only spinner in the app (DESIGN.md §5.27): 12px, inside a busy button,
 * after 200ms. Whole views use skeletons; answers use the trail line.
 */
export function Spinner({ className }: { className?: string }) {
  return <span className={clsx(s.spinner, 'spinner', className)} aria-hidden="true" />
}
