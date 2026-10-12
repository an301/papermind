/**
 * An answer's body (DESIGN.md §5.13, §5.14): markdown with KaTeX math, the
 * writer's [S#] markers as citation chips and [P#] markers as external-paper
 * references.
 *
 * Streaming: the text is split into top-level blocks; finished blocks are
 * memoised (their KaTeX never re-runs) and only the last block re-parses on
 * each delta. The last block hides syntax still being typed, so the tail
 * never flashes a raw "[S" or a KaTeX parse error.
 */
import 'katex/dist/katex.min.css'
import { createContext, memo, useContext, useMemo, type ComponentProps } from 'react'
import Markdown, { type Components } from 'react-markdown'
import rehypeKatex from 'rehype-katex'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import remarkParse from 'remark-parse'
import { unified } from 'unified'
import type { ExternalRef, SourceRef } from '../../../api/types'
import { clsx } from '../../../lib/clsx'
import { CitationChip } from './CitationChip'
import { ExternalCite } from './ExternalCite'
import { remarkMarkers, remarkTypography } from './remarkMarkers'
import s from './AnswerMarkdown.module.css'

export interface MarkerContextValue {
  messageId: string | null
  sources: readonly SourceRef[]
  external: readonly ExternalRef[]
  /** Label → numeral by first appearance in the whole answer, so memoised blocks stay consistent. */
  numerals: Map<string, number>
  /** Sections cited by more than one chip: their chips also show the page. */
  sharedSections: Set<string>
}

const MarkerContext = createContext<MarkerContextValue>({
  messageId: null,
  sources: [],
  external: [],
  numerals: new Map(),
  sharedSections: new Set(),
})

function Marker(props: ComponentProps<'cite'> & { 'data-label'?: string; 'data-repeat'?: boolean | string }) {
  const ctx = useContext(MarkerContext)
  const label = props['data-label'] ?? ''
  if (label.startsWith('P')) {
    const ref = ctx.external.find((e) => e.label === label)
    return ref ? <ExternalCite paper={ref} /> : null
  }
  const source = ctx.sources.find((x) => x.label === label)
  if (!source) return null // the backend strips unknown labels; never show a dead chip
  const numeral = ctx.numerals.get(label) ?? 0
  return (
    <CitationChip
      source={source}
      numeral={numeral}
      messageId={ctx.messageId}
      withPage={!!source.section_number && ctx.sharedSections.has(source.section_number)}
      compact={!!props['data-repeat']}
    />
  )
}

function ExternalLink({ href, children, ...rest }: ComponentProps<'a'>) {
  const external = !!href && /^https?:/.test(href)
  return (
    <a href={href} {...rest} {...(external ? { target: '_blank', rel: 'noopener noreferrer' } : {})}>
      {children}
    </a>
  )
}

/** Images aren't rendered (§5.14): their alt text stands in. */
function Img({ alt }: ComponentProps<'img'>) {
  return alt ? <span className={s.alt}>{alt}</span> : null
}

const remarkPlugins = [remarkGfm, remarkMath, remarkMarkers, remarkTypography]
const rehypePlugins: ComponentProps<typeof Markdown>['rehypePlugins'] = [
  [rehypeKatex, { throwOnError: false, strict: 'ignore', output: 'htmlAndMathml' }],
]
const components: Components = {
  cite: Marker,
  a: ExternalLink,
  img: Img,
  h1: (p) => <h2 {...p} />,
  h4: (p) => <h3 {...p} />,
  h5: (p) => <h3 {...p} />,
  h6: (p) => <h3 {...p} />,
  hr: () => null,
  table: (p) => (
    <div className={s.tableWrap}>
      <table {...p} />
    </div>
  ),
}

const blockParser = unified().use(remarkParse).use(remarkGfm).use(remarkMath)

/** Top-level markdown blocks, sliced from the source so each can be rendered (and memoised) alone. */
export function splitBlocks(md: string): string[] {
  const tree = blockParser.parse(md)
  return tree.children.flatMap((n) => (n.position ? [md.slice(n.position.start.offset, n.position.end.offset)] : []))
}

/** Models emit \( \) and \[ \]; remark-math only knows $ and $$. Converted outside code. */
export function normalizeMathDelimiters(md: string): string {
  return md
    .split(/(```[\s\S]*?(?:```|$)|`[^`\n]*`)/g)
    .map((seg, i) =>
      i % 2
        ? seg
        : seg
            .replace(/\\\[([\s\S]*?)\\\]/g, (_, m: string) => `\n$$\n${m.trim()}\n$$\n`)
            .replace(/\\\(([\s\S]*?)\\\)/g, (_, m: string) => `$${m.trim()}$`),
    )
    .join('')
}

/** Hide what is still being typed at the end of a streaming answer: a half marker, unclosed math. */
export function trimIncompleteTail(md: string): string {
  let out = md.replace(/\[(?:[SP]\d*)?$/, '')
  for (const [open, close] of [
    ['\\[', '\\]'],
    ['\\(', '\\)'],
  ] as const) {
    const o = out.lastIndexOf(open)
    if (o > out.lastIndexOf(close)) out = out.slice(0, o)
  }
  if ((out.match(/\$\$/g)?.length ?? 0) % 2) out = out.slice(0, out.lastIndexOf('$$'))
  const lastLine = out.slice(out.lastIndexOf('\n') + 1).replace(/\$\$/g, '')
  if ((lastLine.match(/(?<!\\)\$/g)?.length ?? 0) % 2) out = out.slice(0, out.lastIndexOf('$'))
  return out
}

/** Label → numeral in order of first appearance; labels never used keep source order after those (§5.15). */
export function markerNumerals(text: string, sources: readonly SourceRef[]): Map<string, number> {
  const order: string[] = []
  for (const m of text.matchAll(/\[(S\d+)\]/g)) if (!order.includes(m[1]!)) order.push(m[1]!)
  for (const src of sources) if (!order.includes(src.label)) order.push(src.label)
  return new Map(order.map((label, i) => [label, i + 1]))
}

const Block = memo(function Block({ text }: { text: string }) {
  return (
    <Markdown remarkPlugins={remarkPlugins} rehypePlugins={rehypePlugins} components={components}>
      {text}
    </Markdown>
  )
})

export interface AnswerMarkdownProps {
  text: string
  streaming: boolean
  sources: readonly SourceRef[]
  external?: readonly ExternalRef[]
  messageId: string | null
  className?: string
}

export function AnswerMarkdown({ text, streaming, sources, external = [], messageId, className }: AnswerMarkdownProps) {
  const md = useMemo(() => normalizeMathDelimiters(text), [text])
  const blocks = useMemo(() => {
    const b = splitBlocks(md)
    if (streaming && b.length) b[b.length - 1] = trimIncompleteTail(b[b.length - 1]!)
    return b
  }, [md, streaming])

  const ctx = useMemo<MarkerContextValue>(() => {
    const numerals = markerNumerals(md, sources)
    const cited = new Set([...md.matchAll(/\[(S\d+)\]/g)].map((m) => m[1]!))
    const bySection = new Map<string, Set<string>>()
    for (const src of sources) {
      if (!src.section_number || !cited.has(src.label)) continue
      const ids = bySection.get(src.section_number) ?? new Set<string>()
      ids.add(src.chunk_id)
      bySection.set(src.section_number, ids)
    }
    const sharedSections = new Set([...bySection].filter(([, ids]) => ids.size > 1).map(([section]) => section))
    return { messageId, sources, external, numerals, sharedSections }
  }, [md, sources, external, messageId])

  return (
    <MarkerContext value={ctx}>
      <div className={clsx(s.answer, !streaming && 'prose-done', className)}>
        {blocks.map((b, i) => (
          <Block key={i} text={b} />
        ))}
      </div>
    </MarkerContext>
  )
}
