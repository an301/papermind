import type { Root, Text } from 'mdast'
import remarkParse from 'remark-parse'
import { unified } from 'unified'
import { visit } from 'unist-util-visit'
import { describe, expect, it } from 'vitest'
import { remarkMarkers } from './remarkMarkers'

/** Each chip in reading order as "S1" or "S1*" (a repeat that shows only its numeral). */
function chips(md: string): string[] {
  const processor = unified().use(remarkParse).use(remarkMarkers)
  const tree = processor.runSync(processor.parse(md)) as Root
  const out: string[] = []
  visit(tree, 'text', (node: Text) => {
    const props = node.data?.hProperties as { dataLabel?: string; dataRepeat?: boolean } | undefined
    if (props?.dataLabel) out.push(props.dataLabel + (props.dataRepeat ? '*' : ''))
  })
  return out
}

describe('remarkMarkers', () => {
  it('shows a source’s location once per paragraph', () => {
    expect(chips('One [S1]. Two [S1]. Three [S2] and [S1].')).toEqual(['S1', 'S1*', 'S2', 'S1*'])
  })

  it('starts again in each paragraph and list item', () => {
    expect(chips('First [S1] and [S1].\n\nSecond [S1].\n\n- a [S2] [S2]\n- b [S2]')).toEqual(['S1', 'S1*', 'S1', 'S2', 'S2*', 'S2'])
  })

  it('counts markers inside emphasis as part of the paragraph', () => {
    expect(chips('Plain [S3], *stressed [S3]*.')).toEqual(['S3', 'S3*'])
  })

  it('never compacts external-paper markers', () => {
    expect(chips('See [P1] and [P1].')).toEqual(['P1', 'P1'])
  })
})
