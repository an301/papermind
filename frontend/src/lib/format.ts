/**
 * Small formatters that carry the house typography (DESIGN.md §7.1):
 * curly quotes, the … character, en dashes in ranges, and a no-break space
 * between numbers and units ("p. 4", "50 MB", "3.1 s").
 */
import type { IsoDate, IsoDateTime } from '../api/types'

export const NBSP = ' '

/** "Vaswani et al., 2017" · "Vaswani and Shazeer, 2017" · "Vaswani, 2017". */
export function authorsShort(authors: readonly string[], year?: number | null): string {
  const surnames = authors.map(surname).filter(Boolean)
  let who = ''
  if (surnames.length === 1) who = surnames[0]!
  else if (surnames.length === 2) who = `${surnames[0]} and ${surnames[1]}`
  else if (surnames.length > 2) who = `${surnames[0]} et${NBSP}al.`
  if (!who) return year ? String(year) : ''
  return year ? `${who}, ${year}` : who
}

/** "Vaswani et al. (2017)" for the add-paper confirmation and Copy with citations. */
export function authorsCitation(authors: readonly string[], year?: number | null): string {
  const short = authorsShort(authors)
  return year ? `${short} (${year})` : short
}

function surname(name: string): string {
  const parts = name.trim().split(/\s+/)
  return parts[parts.length - 1] ?? ''
}

export const yearOf = (date?: IsoDate | null): number | null => (date ? Number(date.slice(0, 4)) || null : null)

/** "Oct 3" this year, "Oct 3, 2025" otherwise. */
export function shortDate(iso: IsoDateTime | IsoDate, now = new Date()): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const sameYear = d.getFullYear() === now.getFullYear()
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', ...(sameYear ? {} : { year: 'numeric' }) })
}

/** "15 pages", "1 page". */
export const pagesLabel = (n: number): string => `${n}${NBSP}${n === 1 ? 'page' : 'pages'}`

/** "p. 4" from a 0-based page index. */
export const pageLabel = (index0: number): string => `p.${NBSP}${index0 + 1}`

/** "p. 4" or "pp. 4–5" from a 0-based range. */
export function pageRangeLabel(start0: number, end0: number): string {
  return end0 > start0 ? `pp.${NBSP}${start0 + 1}–${end0 + 1}` : pageLabel(start0)
}

/** "72 MB" (whole megabytes above 10, one decimal below). */
export function megabytes(bytes: number): string {
  const mb = bytes / (1024 * 1024)
  const text = mb >= 10 ? Math.round(mb).toString() : (Math.round(mb * 10) / 10).toString()
  return `${text}${NBSP}MB`
}

/** "3,712" with the reader's grouping. */
export const count = (n: number): string => n.toLocaleString('en-US')

/** "a, b and c". */
export function listJoin(items: readonly string[]): string {
  if (items.length <= 1) return items.join('')
  return `${items.slice(0, -1).join(', ')} and ${items[items.length - 1]}`
}

/** "1 concept" / "42 concepts". */
export const plural = (n: number, one: string, many = `${one}s`): string => `${count(n)} ${n === 1 ? one : many}`

/** Wrap in curly single quotes, as UI copy quotes a name or a query: ‘diffusion’. */
export const singleQuoted = (s: string): string => `‘${s}’`

/** Straight quotes and three dots to their typographic forms (for server strings shown as-is). */
export function smarten(text: string): string {
  return text
    .replace(/\.\.\./g, '…')
    .replace(/(^|[\s([{“‘-])"/g, '$1“')
    .replace(/"/g, '”')
    .replace(/(^|[\s([{“-])'/g, '$1‘')
    .replace(/'/g, '’')
}

/** Filename without the .pdf extension, for uploads with no metadata. */
export const stripPdfExtension = (name: string): string => name.replace(/\.pdf$/i, '')
