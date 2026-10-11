/**
 * Corrections in "What you know" (DESIGN.md §5.22): "I know this", "Forget
 * this" and adding a concept are optimistic, with an Undo that lasts 6 s.
 *
 * The server can't put an inferred edge back the way it was (POST always
 * writes a self-reported 1.0), so nothing is sent until the Undo window
 * closes: the shared query cache changes at once (so every screen agrees),
 * and the write goes out when the window ends, when the reader leaves the
 * page, or never if they press Undo. A failed write puts the row back and
 * says "Couldn’t save that. Try again".
 */
import { create } from 'zustand'
import { api } from '../../api/client'
import { qk } from '../../api/keys'
import { queryClient } from '../../api/queryClient'
import type { ConceptInfo, KnownConcept, UserProfile } from '../../api/types'
import { announce } from '../../lib/announce'

export const UNDO_MS = 6000

export type CorrectionKind = 'know' | 'forget' | 'add'

export interface Correction {
  kind: CorrectionKind
  conceptId: string
  name: string
  /** The edge before the change (null for a concept that wasn't in the list). */
  previous: KnownConcept | null
  /** 'pending' while Undo is offered; 'failed' when the write didn't take. */
  status: 'pending' | 'failed'
}

interface CorrectionsState {
  byConcept: Record<string, Correction>
}

export const useCorrections = create<CorrectionsState>()(() => ({ byConcept: {} }))

const timers = new Map<string, number>()

const userId = (): string | undefined => queryClient.getQueryData<UserProfile>(qk.user)?.user_id
const listKey = () => qk.knowledge(userId() ?? '')
const enc = encodeURIComponent

function setList(fn: (list: KnownConcept[]) => KnownConcept[]): void {
  queryClient.setQueryData<KnownConcept[]>(listKey(), (list) => fn(list ?? []))
}

const byName = (a: KnownConcept, b: KnownConcept) => a.name.localeCompare(b.name, undefined, { sensitivity: 'base' })

function put(c: Correction | null, conceptId: string): void {
  useCorrections.setState((s) => {
    const next = { ...s.byConcept }
    if (c) next[conceptId] = c
    else delete next[conceptId]
    return { byConcept: next }
  })
}

function selfReported(id: string, name: string, area: string | null | undefined): KnownConcept {
  return {
    concept_id: id,
    name,
    area: area ?? null,
    confidence: 1,
    source: 'self_reported',
    evidence: 'The reader said they know this.',
    updated_at: new Date().toISOString(),
  }
}

function schedule(conceptId: string): void {
  window.clearTimeout(timers.get(conceptId))
  timers.set(
    conceptId,
    window.setTimeout(() => void commit(conceptId), UNDO_MS),
  )
}

/** Send the write for one correction now. */
export async function commit(conceptId: string, keepalive = false): Promise<void> {
  window.clearTimeout(timers.get(conceptId))
  timers.delete(conceptId)
  const c = useCorrections.getState().byConcept[conceptId]
  const uid = userId()
  if (!c || !uid) return
  try {
    if (c.kind === 'forget') {
      await api<void>(`/users/${enc(uid)}/knowledge/${enc(conceptId)}`, { method: 'DELETE', keepalive })
      setList((list) => list.filter((k) => k.concept_id !== conceptId))
    } else {
      const edge = await api<KnownConcept>(`/users/${enc(uid)}/knowledge`, { method: 'POST', body: { concept: conceptId }, keepalive })
      setList((list) => [...list.filter((k) => k.concept_id !== conceptId), edge].sort(byName))
    }
    put(null, conceptId)
  } catch {
    // Put the row back the way it was and say so in place.
    setList((list) => {
      const rest = list.filter((k) => k.concept_id !== conceptId)
      return c.previous ? [...rest, c.previous].sort(byName) : rest
    })
    put({ ...c, status: 'failed' }, conceptId)
  }
}

if (typeof window !== 'undefined') {
  window.addEventListener('pagehide', () => {
    for (const [id, c] of Object.entries(useCorrections.getState().byConcept)) if (c.status === 'pending') void commit(id, true)
  })
}

/** "I know this": Comfortable, as something the reader said. */
export function markKnown(k: KnownConcept): void {
  const previous = useCorrections.getState().byConcept[k.concept_id]?.previous ?? k
  setList((list) => list.map((x) => (x.concept_id === k.concept_id ? selfReported(k.concept_id, k.name, k.area) : x)))
  put({ kind: 'know', conceptId: k.concept_id, name: k.name, previous, status: 'pending' }, k.concept_id)
  announce('Marked as known.')
  schedule(k.concept_id)
}

/** "Forget this". The row stays, saying "Forgot softmax. Undo", until the window closes. */
export function forget(k: KnownConcept): void {
  const previous = useCorrections.getState().byConcept[k.concept_id]?.previous ?? k
  put({ kind: 'forget', conceptId: k.concept_id, name: k.name, previous, status: 'pending' }, k.concept_id)
  announce(`Forgot ${k.name}.`)
  schedule(k.concept_id)
}

/** "Add something you know": appears at once as Comfortable. */
export function addConcept(c: ConceptInfo): void {
  const existing = queryClient.getQueryData<KnownConcept[]>(listKey())?.find((k) => k.concept_id === c.concept_id) ?? null
  setList((list) => [...list.filter((k) => k.concept_id !== c.concept_id), selfReported(c.concept_id, c.name, c.area)].sort(byName))
  put({ kind: existing ? 'know' : 'add', conceptId: c.concept_id, name: c.name, previous: existing, status: 'pending' }, c.concept_id)
  announce(`Added ${c.name} to what you know.`)
  schedule(c.concept_id)
}

/** Undo: nothing was sent yet, so put the cache back. */
export function undo(conceptId: string): void {
  window.clearTimeout(timers.get(conceptId))
  timers.delete(conceptId)
  const c = useCorrections.getState().byConcept[conceptId]
  if (!c) return
  setList((list) => {
    const rest = list.filter((k) => k.concept_id !== conceptId)
    return c.previous ? [...rest, c.previous].sort(byName) : rest
  })
  put(null, conceptId)
  announce('Undone.')
}

/** "Try again" after a failure: re-apply and send at once. */
export function retry(conceptId: string): void {
  const c = useCorrections.getState().byConcept[conceptId]
  if (!c) return
  if (c.kind !== 'forget') {
    setList((list) => [...list.filter((k) => k.concept_id !== conceptId), selfReported(conceptId, c.name, c.previous?.area)].sort(byName))
  }
  put({ ...c, status: 'pending' }, conceptId)
  void commit(conceptId)
}

export function dismissFailure(conceptId: string): void {
  put(null, conceptId)
}

/** Result line for a row (replaces the evidence line while it lasts). */
export function correctionText(c: Correction): string {
  if (c.status === 'failed') return 'Couldn’t save that.'
  switch (c.kind) {
    case 'know':
      return 'Marked as known.'
    case 'add':
      return 'Added to what you know.'
    case 'forget':
      return `Forgot ${c.name}.`
  }
}
