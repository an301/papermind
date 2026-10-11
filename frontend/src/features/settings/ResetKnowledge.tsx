import { useState } from 'react'
import { useKnowledge, useResetKnowledge } from '../../api/queries'
import { Button } from '../../components/Button'
import { Dialog, DialogClose } from '../../components/Dialog'
import { toast } from '../../components/Toast'
import { plural } from '../../lib/format'
import s from './SettingsPage.module.css'

/**
 * "Reset what you know…" (§4.7, §5.23): a --danger text button that opens a
 * confirmation. Initial focus is on Cancel, the least destructive control.
 */
export function ResetKnowledge() {
  const knowledge = useKnowledge()
  const resetAll = useResetKnowledge()
  const [open, setOpen] = useState(false)
  const busy = resetAll.isPending
  const [error, setError] = useState<string | null>(null)
  const n = knowledge.data?.length ?? 0

  async function reset() {
    setError(null)
    const { failed } = await resetAll.mutateAsync((knowledge.data ?? []).map((k) => k.concept_id))
    if (failed) {
      setError(`Couldn’t forget ${plural(failed, 'concept')}. Try again to finish.`)
      return
    }
    setOpen(false)
    toast({ message: 'Reset what you know.' })
  }

  return (
    <>
      <Button variant="text" tone="danger" flush className={s.start} onClick={() => setOpen(true)}>
        Reset what you know…
      </Button>
      <Dialog
        open={open}
        onOpenChange={(o) => {
          if (!busy) setOpen(o)
          if (!o) setError(null)
        }}
        title="Reset what you know?"
        description={
          n > 0
            ? `This forgets all ${plural(n, 'concept')}. Your papers, highlights and pins stay.`
            : 'There’s nothing in what you know yet, so there’s nothing to forget. Your papers, highlights and pins stay either way.'
        }
        initialFocus="[data-autofocus]"
        actions={
          <>
            <DialogClose asChild>
              <Button variant="secondary" data-autofocus>
                Cancel
              </Button>
            </DialogClose>
            <Button variant="danger" busy={busy} busyLabel="Resetting…" onClick={() => void reset()}>
              Reset
            </Button>
          </>
        }
      >
        {error ? (
          <p className={s.dialogError} role="alert">
            {error}
          </p>
        ) : null}
      </Dialog>
    </>
  )
}
