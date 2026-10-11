import { Fragment } from 'react'
import { create } from 'zustand'
import { Button } from '../components/Button'
import { Dialog, DialogClose } from '../components/Dialog'
import { Kbd } from '../components/Kbd'
import { ALT_KEY, chord, ENTER_KEY, MOD_KEY, SHIFT_KEY } from '../lib/platform'
import s from './ShortcutsDialog.module.css'

interface ShortcutsState {
  open: boolean
  /** Opened from the keyboard (`?`): appear without animation (§5.23). */
  instant: boolean
}

const useShortcuts = create<ShortcutsState>()(() => ({ open: false, instant: false }))

/** Open the keyboard shortcuts sheet (the `?` key, Settings, the Reader's More menu). */
export function openShortcuts(options: { instant?: boolean } = {}): void {
  useShortcuts.setState({ open: true, instant: options.instant ?? false })
}

type Row = { keys: string[][]; action: string; joiner?: string }

/** DESIGN.md §8.2. Each inner array is one way to press it; chords are glued with a no-break space. */
const GROUPS: { title: string; rows: Row[] }[] = [
  {
    title: 'Everywhere',
    rows: [
      { keys: [['?']], action: 'Keyboard shortcuts' },
      { keys: [['Esc']], action: 'Close the topmost popover, menu, card or dialog; otherwise stop a streaming answer' },
      { keys: [['F6'], [chord(SHIFT_KEY, 'F6')]], action: 'Next or previous region', joiner: '/' },
    ],
  },
  {
    title: 'Reading',
    rows: [
      { keys: [['←', '→'], ['PageUp', 'PageDown']], action: 'Previous or next page' },
      { keys: [['Home', 'End']], action: 'First or last page' },
      { keys: [[chord(MOD_KEY, 'F')]], action: 'Find in paper' },
      { keys: [[ENTER_KEY], [chord(SHIFT_KEY, ENTER_KEY)]], action: 'Next or previous match, in find', joiner: '/' },
      { keys: [[chord(MOD_KEY, '+')], [chord(MOD_KEY, '−')], [chord(MOD_KEY, '0')]], action: 'Zoom in, zoom out, fit width' },
      { keys: [['[']], action: 'Show or hide contents' },
      { keys: [[']']], action: 'Show or hide the assistant' },
      { keys: [[chord(ALT_KEY, '←')]], action: 'Back to where you were before a citation jump' },
      { keys: [['E']], action: 'Explain the selection' },
      { keys: [['A']], action: 'Ask about the selection' },
      { keys: [['H']], action: 'Highlight the selection with the last colour' },
      { keys: [['1', '2', '3', '4']], action: 'Highlight yellow, green, blue or orange' },
      { keys: [['/']], action: 'Focus the question box' },
    ],
  },
  {
    title: 'Assistant',
    rows: [
      { keys: [[ENTER_KEY], [chord(MOD_KEY, ENTER_KEY)]], action: 'Ask' },
      { keys: [[chord(SHIFT_KEY, ENTER_KEY)]], action: 'New line' },
      { keys: [['Esc']], action: 'In the question box: stop the answer, remove the quote, or go back to the page' },
      { keys: [[chord(ALT_KEY, '↑')], [chord(ALT_KEY, '↓')]], action: 'Previous or next answer', joiner: '/' },
      { keys: [['←', '→', 'Home', 'End']], action: 'Change depth, on the Depth control' },
      { keys: [[ENTER_KEY]], action: 'On a citation: show the passage and move to its bracket' },
      { keys: [[ENTER_KEY], ['Esc']], action: 'On a bracket: back to the answer’s citation', joiner: '/' },
    ],
  },
]

export function ShortcutsDialog() {
  const { open, instant } = useShortcuts()
  return (
    <Dialog
      open={open}
      onOpenChange={(o) => useShortcuts.setState({ open: o })}
      title="Keyboard shortcuts"
      wide
      instant={instant}
      actions={
        <DialogClose asChild>
          <Button variant="secondary">Close</Button>
        </DialogClose>
      }
    >
      <p className={s.note}>Letter keys work outside text fields, and can be turned off in Settings.</p>
      {GROUPS.map((g) => (
        <section key={g.title} className={s.group} aria-labelledby={`kb-${g.title}`}>
          <h3 id={`kb-${g.title}`} className={s.heading}>
            {g.title}
          </h3>
          <table className={s.table}>
            <tbody>
              {g.rows.map((r) => (
                <tr key={r.action}>
                  <td className={s.keys}>
                    {r.keys.map((alt, i) => (
                      <Fragment key={i}>
                        {i > 0 ? <span className={s.joiner}>{r.joiner ?? ''}</span> : null}
                        {alt.map((k) => (
                          <Kbd key={k}>{k}</Kbd>
                        ))}
                      </Fragment>
                    ))}
                  </td>
                  <td className={s.action}>{r.action}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ))}
    </Dialog>
  )
}
