/**
 * The three depths, named for the answer rather than the reader (DESIGN.md
 * §1.3, §5.9). Internal values are the API's `Level`.
 */
import type { Level } from '../../api/types'

export interface LevelOption {
  value: Level
  /** Segmented control and radio label. */
  label: 'From basics' | 'Standard' | 'Technical'
  /** Onboarding and Settings: what answers will do (§4.6). */
  description: string
  /** Depth control tooltip (§5.9). */
  tooltip: string
}

export const LEVEL_OPTIONS: readonly LevelOption[] = [
  {
    value: 'beginner',
    label: 'From basics',
    description: 'Define terms as they come up and lean on intuition. Math only when it helps.',
    tooltip: 'Defines terms as they come up and leans on intuition. Math only when it helps.',
  },
  {
    value: 'intermediate',
    label: 'Standard',
    description: 'Assume neural nets and backprop. Explain this paper’s new ideas and terms.',
    tooltip: 'Assumes neural nets and backprop. Explains this paper’s new ideas and terms.',
  },
  {
    value: 'expert',
    label: 'Technical',
    description: 'Skip the background. Be precise, show the math, compare with related work.',
    tooltip: 'Skips the background. Precise, shows the math, compares with related work.',
  },
]

export const levelLabel = (level: Level): LevelOption['label'] =>
  LEVEL_OPTIONS.find((o) => o.value === level)?.label ?? 'Standard'
