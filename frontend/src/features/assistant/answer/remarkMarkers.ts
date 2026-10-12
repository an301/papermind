/**
 * The writer marks claims taken from the paper with [S1], [S2] … and papers it
 * looked up on Semantic Scholar with [P1] … (papermind/agent/prompts/writer.md).
 * This remark plugin turns each marker in prose into an inline element the
 * renderer maps to a chip (§5.15). Markers inside code are not text nodes, so
 * they stay as written.
 */
import type { Parent, Root, RootContent, Text } from 'mdast'
import { visit } from 'unist-util-visit'

export const MARKER_RE = /\[([SP]\d+)\]/g

export function remarkMarkers() {
  return (tree: Root) => {
    visit(tree, 'text', (node: Text, index, parent) => {
      if (!parent || index === undefined || !/\[[SP]\d+\]/.test(node.value)) return
      const parts: Text[] = []
      let last = 0
      for (const m of node.value.matchAll(MARKER_RE)) {
        const at = m.index ?? 0
        if (at > last) parts.push({ type: 'text', value: node.value.slice(last, at) })
        const label = m[1]!
        parts.push({
          type: 'text',
          value: label,
          // mdast-util-to-hast turns a text node with hName into that element.
          data: { hName: 'cite', hProperties: { dataLabel: label }, hChildren: [] },
        })
        last = at + m[0].length
      }
      if (!parts.length) return
      if (last < node.value.length) parts.push({ type: 'text', value: node.value.slice(last) })
      parent.children.splice(index, 1, ...parts)
      return index + parts.length
    })
    markRepeats(tree)
  }
}

/** Block-level nodes whose chips are read together: a paragraph, a list item's line, a heading, a cell. */
const BLOCKS = new Set(['paragraph', 'heading', 'tableCell'])

/**
 * A second chip for the same source in one paragraph carries only its numeral
 * ("1" rather than "1 §1 pp. 1–2" three times in a row); the first one there
 * already said where it is. It still links to the passage like any chip.
 */
function markRepeats(tree: Root) {
  visit(tree, (node) => {
    if (!BLOCKS.has(node.type)) return
    const seen = new Set<string>()
    const walk = (n: RootContent) => {
      const props = n.type === 'text' ? (n.data?.hProperties as Record<string, unknown> | undefined) : undefined
      const label = typeof props?.dataLabel === 'string' ? props.dataLabel : null
      if (label?.startsWith('S')) {
        if (seen.has(label)) props!.dataRepeat = true
        seen.add(label)
      }
      if ('children' in n) for (const child of (n as Parent).children) walk(child)
    }
    for (const child of (node as Parent).children) walk(child)
    return 'skip'
  })
}

/** Typography pass for prose the model wrote (§5.14): curly quotes, ellipses, no-break spaces in "p. 4". */
export function remarkTypography() {
  return (tree: Root) => {
    visit(tree, 'text', (node: Text) => {
      node.value = node.value
        .replace(/\.\.\./g, '…')
        .replace(/ -- /g, ' – ')
        .replace(/(^|[\s([{“‘—–-])"/g, '$1“')
        .replace(/"/g, '”')
        .replace(/(^|[\s([{“—–-])'/g, '$1‘')
        .replace(/'/g, '’')
        .replace(/\b(p|pp|Fig|Eq|Sec|Table)\. (?=\d)/g, '$1. ')
        .replace(/(\d) (s|ms|MB|GB|%)\b/g, '$1 $2')
    })
  }
}
