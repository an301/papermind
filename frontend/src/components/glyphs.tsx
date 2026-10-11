/**
 * The four custom glyphs from DESIGN.md §3, drawn on Phosphor's 256 grid with
 * a 16-unit stroke and round caps and joins, so they sit beside Phosphor icons
 * as one set. They take `currentColor`; callers colour them (--pencil for the
 * assistant's marks, --ink-2 for HighlighterGlyph).
 */
import type { ReactNode, SVGProps } from 'react'

export interface GlyphProps extends Omit<SVGProps<SVGSVGElement>, 'children'> {
  size?: number
}

function Glyph({ size = 16, children, ...rest }: GlyphProps & { children: ReactNode }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 256 256"
      fill="none"
      stroke="currentColor"
      strokeWidth={16}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...rest}
    >
      {children}
    </svg>
  )
}

/** The pencil bracket. Only in the one-time legend, where it depicts the mark it explains. */
export function BracketGlyph(props: GlyphProps) {
  return (
    <Glyph {...props}>
      <path d="M112 40H176V216H112" />
    </Glyph>
  )
}

/** ‸ The gap callout's insert caret and the learning-path destination. */
export function CaretInsertGlyph(props: GlyphProps) {
  return (
    <Glyph {...props}>
      <polyline points="72,184 128,72 184,184" />
    </Glyph>
  )
}

/** A bracket at 70% with a filled dot on its top tick: "Pin to page" and pinned brackets. */
export function PinToPageGlyph(props: GlyphProps) {
  return (
    <Glyph {...props}>
      <path d="M116.8 66.4H161.6V189.6H116.8" />
      <circle cx="116.8" cy="66.4" r="20" fill="currentColor" stroke="none" />
    </Glyph>
  )
}

/** A chisel-tip marker: the "Your marks" heading in the outline. */
export function HighlighterGlyph(props: GlyphProps) {
  return (
    <Glyph {...props}>
      <polygon points="64,184 152,96 192,136 104,224" />
      <polygon points="40,216 64,184 104,224" />
    </Glyph>
  )
}
