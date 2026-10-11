import type { ElementType, ReactNode } from 'react'

const style = {
  position: 'absolute',
  width: 1,
  height: 1,
  padding: 0,
  margin: -1,
  overflow: 'hidden',
  clip: 'rect(0 0 0 0)',
  clipPath: 'inset(50%)',
  whiteSpace: 'nowrap',
  border: 0,
} as const

/** Text for screen readers only. */
export function VisuallyHidden({ children, as: Tag = 'span', id }: { children: ReactNode; as?: ElementType; id?: string }) {
  return (
    <Tag id={id} style={style}>
      {children}
    </Tag>
  )
}
