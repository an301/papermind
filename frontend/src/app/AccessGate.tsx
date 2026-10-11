import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { getAccessCode, onAccessRejected, setAccessCode } from '../api/access'
import { api, isApiError, NetworkError } from '../api/client'
import { useHealth } from '../api/queries'
import { Button } from '../components/Button'
import { TextField } from '../components/TextField'
import p from './Page.module.css'
import s from './AccessGate.module.css'

/**
 * On a public server with an access code (DEPLOY.md), asks for the code before
 * anything else and keeps the rest of the app unmounted until it works, so no
 * screen fires requests that would only be refused. Without a code configured
 * (local dev) it renders its children and nothing else.
 */
export function AccessGate({ children }: { children: ReactNode }) {
  const health = useHealth()
  const [rejected, setRejected] = useState(false)
  const [, rerender] = useState(0)

  useEffect(
    () =>
      onAccessRejected(() => {
        setAccessCode(null)
        setRejected(true)
      }),
    [],
  )

  // Until health answers we can't know whether a code is needed; rendering the
  // app early would fire requests a gated server refuses.
  if (health.isPending) return null
  const required = health.data?.access_code_required === true
  if (!required || (getAccessCode() && !rejected)) return <>{children}</>
  return (
    <GateScreen
      expired={rejected}
      onUnlocked={() => {
        setRejected(false)
        rerender((n) => n + 1)
      }}
    />
  )
}

function GateScreen({ expired, onUnlocked }: { expired: boolean; onUnlocked: () => void }) {
  const queryClient = useQueryClient()
  const [code, setCode] = useState('')
  const [error, setError] = useState<string | null>(expired ? 'That code no longer works. Ask for the current one.' : null)
  const [checking, setChecking] = useState(false)
  const input = useRef<HTMLInputElement>(null)

  useEffect(() => input.current?.focus(), [])

  async function submit(e: FormEvent) {
    e.preventDefault()
    const trimmed = code.trim()
    if (!trimmed) {
      setError('Enter the access code.')
      input.current?.focus()
      return
    }
    setChecking(true)
    setError(null)
    setAccessCode(trimmed)
    try {
      await api<void>('/access')
      // Anything that was refused before the code was set is fetched again.
      await queryClient.invalidateQueries()
      onUnlocked()
    } catch (err) {
      setAccessCode(null)
      setError(
        isApiError(err, 'access_code_invalid')
          ? 'That code isn’t right. Check it and try again.'
          : isApiError(err)
            ? err.message
            : err instanceof NetworkError
              ? 'The server didn’t respond. Check your connection and try again.'
              : 'The code couldn’t be checked. Try again.',
      )
      input.current?.select()
    } finally {
      setChecking(false)
    }
  }

  return (
    <div className={s.screen}>
      <header className={s.header}>
        <span className={s.wordmark}>PaperMind</span>
      </header>
      <main className={p.page}>
        <div className={p.narrow}>
          <h1 className={p.title}>Enter your access code</h1>
          <p className={p.subtitle}>
            This copy of PaperMind is shared with a small group. Ask the person who sent you the link for the code.
          </p>
          <form className={s.form} onSubmit={(e) => void submit(e)} noValidate>
            <TextField
              ref={input}
              label="Access code"
              value={code}
              onChange={(e) => {
                setCode(e.target.value)
                if (error) setError(null)
              }}
              error={error}
              autoComplete="off"
              autoCapitalize="none"
              spellCheck={false}
              size="lg"
            />
            <Button type="submit" variant="primary" size="lg" busy={checking} busyLabel="Checking…">
              Continue
            </Button>
          </form>
        </div>
      </main>
    </div>
  )
}
