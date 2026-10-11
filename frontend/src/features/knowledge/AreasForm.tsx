import { useState } from 'react'
import { useAddOnboardingAreas, useConceptAreas } from '../../api/queries'
import { Button } from '../../components/Button'
import { Checkbox } from '../../components/Choice'
import { Skeleton } from '../../components/Skeleton'
import { announce } from '../../lib/announce'
import { smarten } from '../../lib/format'
import s from './KnowledgePage.module.css'

/**
 * The empty profile (§5.22): "Mark the areas you’re already comfortable with:"
 * as 12 checkboxes (two columns from 640px) and Save. Same write as
 * onboarding step 2: POST /users/{id}/knowledge/areas.
 */
export function AreasForm() {
  const areas = useConceptAreas()
  const save = useAddOnboardingAreas()
  const [chosen, setChosen] = useState<Set<string>>(new Set())
  const [error, setError] = useState<string | null>(null)
  return (
    <form
      className={s.areasForm}
      onSubmit={async (e) => {
        e.preventDefault()
        setError(null)
        if (!chosen.size) {
          setError('Choose at least one area, or come back once you’ve asked a few questions.')
          return
        }
        try {
          await save.mutateAsync([...chosen])
          announce(chosen.size === 1 ? 'Added 1 area to what you know.' : `Added ${chosen.size} areas to what you know.`)
        } catch {
          setError('Couldn’t save that. Try again.')
        }
      }}
    >
      <fieldset>
        <legend className={s.emptyLead}>Mark the areas you’re already comfortable with:</legend>
        <div className={s.areaGrid}>
          {areas.isPending
            ? Array.from({ length: 12 }, (_, i) => (
                <div key={i} className={s.areaSkeleton}>
                  <Skeleton width="16px" height={16} />
                  <Skeleton width={`${45 + ((i * 13) % 35)}%`} height={14} />
                </div>
              ))
            : (areas.data ?? []).map((a) => (
                <Checkbox
                  key={a.area_id}
                  label={a.name}
                  description={smarten(a.description)}
                  checked={chosen.has(a.area_id)}
                  onChange={(e) => {
                    const next = new Set(chosen)
                    if (e.target.checked) next.add(a.area_id)
                    else next.delete(a.area_id)
                    setChosen(next)
                    setError(null)
                  }}
                />
              ))}
        </div>
      </fieldset>
      {areas.isError ? <p className={s.formNote}>The list of areas isn’t available right now. It fills in as you read either way.</p> : null}
      {error ? (
        <p className={s.formNote} role="alert">
          {error}
        </p>
      ) : null}
      <div>
        <Button type="submit" variant="primary" busy={save.isPending} busyLabel="Saving…">
          Save
        </Button>
      </div>
    </form>
  )
}
