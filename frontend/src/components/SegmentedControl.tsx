import { ToggleGroup } from 'radix-ui'
import { useRef, useState, type CSSProperties } from 'react'
import { clsx } from '../lib/clsx'
import s from './SegmentedControl.module.css'
import { Tooltip } from './Tooltip'

export interface SegmentOption<V extends string> {
  value: V
  label: string
  /** Shown after 600ms (the Depth descriptions, §5.9). */
  tooltip?: string
}

export interface SegmentedControlProps<V extends string> {
  value: V
  onValueChange: (value: V, source: 'pointer' | 'keyboard') => void
  options: readonly SegmentOption<V>[]
  /** Name the group: point at a visible label (preferred) … */
  'aria-labelledby'?: string
  /** … or give it a name directly. */
  'aria-label'?: string
  /** A visible note under the control that explains the current choice. */
  'aria-describedby'?: string
  /** Fixed segment width in px (Depth uses 84); otherwise segments share the widest label's width. */
  segmentWidth?: number
  className?: string
  id?: string
}

/**
 * A single-choice segmented control on Radix ToggleGroup. Keyboard changes
 * (arrows, Home, End) move the thumb instantly; pointer clicks slide it (§6.4).
 * Clicking the selected segment never clears it.
 */
export function SegmentedControl<V extends string>({
  value,
  onValueChange,
  options,
  segmentWidth,
  className,
  id,
  ...aria
}: SegmentedControlProps<V>) {
  const pointer = useRef(false)
  const [animate, setAnimate] = useState(false)
  const index = options.findIndex((o) => o.value === value)
  const style = {
    '--count': options.length,
    '--index': Math.max(0, index),
    ...(segmentWidth ? { '--segment-w': `${segmentWidth}px` } : {}),
  } as CSSProperties

  return (
    <ToggleGroup.Root
      id={id}
      type="single"
      value={value}
      onValueChange={(v) => {
        if (!v) return // Radix clears on a second click; a segmented control never does.
        const source = pointer.current ? 'pointer' : 'keyboard'
        setAnimate(source === 'pointer')
        pointer.current = false
        onValueChange(v as V, source)
      }}
      rovingFocus
      loop={false}
      className={clsx(s.track, !!segmentWidth && s.fixed, className)}
      style={style}
      onPointerDown={() => {
        pointer.current = true
      }}
      onKeyDown={() => {
        pointer.current = false
      }}
      {...aria}
    >
      <span className={clsx(s.thumb, 'depth-thumb')} data-animate={animate || undefined} data-hidden={index < 0 || undefined} aria-hidden="true" />
      {options.map((o) => {
        const item = (
          <ToggleGroup.Item key={o.value} value={o.value} className={s.item} data-label={o.label}>
            {o.label}
          </ToggleGroup.Item>
        )
        return o.tooltip ? (
          <Tooltip key={o.value} label={o.tooltip}>
            {item}
          </Tooltip>
        ) : (
          item
        )
      })}
    </ToggleGroup.Root>
  )
}
