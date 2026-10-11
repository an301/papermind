import { useRef, useState, type ButtonHTMLAttributes } from 'react'
import { clsx } from '../lib/clsx'
import s from './Switch.module.css'

export interface SwitchProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'onChange'> {
  checked: boolean
  onCheckedChange: (checked: boolean) => void
}

/**
 * role="switch". Label it with aria-labelledby (a visible label) rather than
 * text inside the switch.
 */
export function Switch({ checked, onCheckedChange, className, ...rest }: SwitchProps) {
  // Animate only when the change came from a pointer; keyboard toggles are instant (§6.4).
  const pointer = useRef(false)
  const [animate, setAnimate] = useState(false)
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      className={clsx(s.switch, className)}
      data-animate={animate || undefined}
      onPointerDown={() => {
        pointer.current = true
      }}
      onClick={() => {
        setAnimate(pointer.current)
        pointer.current = false
        onCheckedChange(!checked)
      }}
      {...rest}
    >
      <span className={s.thumb} aria-hidden="true" />
    </button>
  )
}
