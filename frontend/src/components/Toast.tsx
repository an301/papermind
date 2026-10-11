import { XIcon } from '@phosphor-icons/react'
import { Toast as T } from 'radix-ui'
import { useRef, type ReactNode } from 'react'
import { create } from 'zustand'
import { Button } from './Button'
import { IconButton } from './IconButton'
import s from './Toast.module.css'

/**
 * Toasts are only for events that happen away from where you are, or that
 * need an Undo (DESIGN.md §5.21). Never toast what's already shown inline.
 *
 *   toast({ message: 'Removed Attention Is All You Need.', action: { label: 'Undo', onAction: restore },
 *           onClose: (reason) => reason !== 'action' && commitDelete() })
 */
export type ToastCloseReason = 'timeout' | 'action' | 'dismiss'

export interface ToastOptions {
  message: ReactNode
  action?: { label: string; onAction: () => void }
  /** ms; 4000 by default, 6000 with an action. `Infinity` = persistent (gets a close button). */
  duration?: number
  /** Called once, however the toast goes away. 'action' means the action button was used. */
  onClose?: (reason: ToastCloseReason) => void
}

interface ToastItem extends ToastOptions {
  id: number
}

interface ToastStore {
  toasts: ToastItem[]
}

const MAX_VISIBLE = 3
let nextId = 1

const useToasts = create<ToastStore>()(() => ({ toasts: [] }))
const closed = new Set<number>()

const remove = (id: number) => useToasts.setState((st) => ({ toasts: st.toasts.filter((t) => t.id !== id) }))

/** `animated`: Radix is already playing the exit fade, so unmount after it (--dur-fast). */
function finish(id: number, reason: ToastCloseReason, animated = false) {
  if (closed.has(id)) return
  closed.add(id)
  const item = useToasts.getState().toasts.find((t) => t.id === id)
  if (animated) window.setTimeout(() => remove(id), 160)
  else remove(id)
  item?.onClose?.(reason)
}

/** Show a toast. Returns its id for `dismissToast`. */
export function toast(options: ToastOptions): number {
  const id = nextId++
  const toasts = [...useToasts.getState().toasts, { ...options, id }]
  useToasts.setState({ toasts })
  // A fourth toast pushes the oldest out; it closes as if its time ran out (so a deferred delete still commits).
  toasts.slice(0, Math.max(0, toasts.length - MAX_VISIBLE)).forEach((t) => finish(t.id, 'timeout'))
  return id
}

export function dismissToast(id: number): void {
  finish(id, 'dismiss')
}

/** Mount once, inside <ToastProvider>. */
export function Toaster() {
  const toasts = useToasts((st) => st.toasts)
  return (
    <>
      {toasts.map((t) => (
        <ToastView key={t.id} item={t} />
      ))}
      <T.Viewport className={s.viewport} label="Notifications ({hotkey})" />
    </>
  )
}

function ToastView({ item }: { item: ToastItem }) {
  const acted = useRef(false)
  const persistent = item.duration === Infinity
  const duration = persistent ? Infinity : (item.duration ?? (item.action ? 6000 : 4000))
  return (
    <T.Root
      className={`${s.toast} toast`}
      type="background"
      duration={duration}
      onOpenChange={(open) => {
        if (!open) finish(item.id, acted.current ? 'action' : persistent ? 'dismiss' : 'timeout', true)
      }}
    >
      <T.Description className={s.message}>{item.message}</T.Description>
      {item.action || persistent ? (
        <div className={s.actions}>
          {item.action ? (
            <T.Action asChild altText={item.action.label}>
              <Button
                variant="text"
                tone="ink"
                onClick={() => {
                  acted.current = true
                  item.action?.onAction()
                }}
              >
                {item.action.label}
              </Button>
            </T.Action>
          ) : null}
          {persistent ? (
            <T.Close asChild>
              <IconButton label="Close" icon={XIcon} noTooltip />
            </T.Close>
          ) : null}
        </div>
      ) : null}
    </T.Root>
  )
}

export function ToastProvider({ children }: { children: ReactNode }) {
  return (
    <T.Provider swipeDirection="left" duration={4000} label="Notification">
      {children}
    </T.Provider>
  )
}
