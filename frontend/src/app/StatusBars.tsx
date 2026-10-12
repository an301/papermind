import { CheckIcon, WarningIcon, WifiSlashIcon } from '@phosphor-icons/react'
import { useEffect, useRef, useState } from 'react'
import { useLocation } from 'react-router'
import { isAnthropicKeyMissing, useHealth } from '../api/queries'
import { Button } from '../components/Button'
import { StatusBar } from '../components/StatusBar'
import { announce } from '../lib/announce'
import { copyText } from '../lib/clipboard'
import { useOnline } from '../lib/useOnline'
import s from './StatusBars.module.css'

const OFFLINE_COPY: Record<string, string> = {
  '/': 'You’re offline. Your papers are still here; adding new ones needs a connection.',
  '/know': 'You’re offline. What you know is still here; changes need a connection.',
  '/settings': 'You’re offline. Settings on this device still work.',
}

export const API_KEY_COMMAND = 'export ANTHROPIC_API_KEY=…'

/**
 * App-level bars under the header (DESIGN.md §5.25): offline, "Back online."
 * for 3 s, and the persistent missing-API-key bar. Each is 32px on --surface
 * with a 1px --line-subtle rule.
 */
export function StatusBars() {
  const online = useOnline()
  const { pathname } = useLocation()
  const health = useHealth()
  const [backOnline, setBackOnline] = useState(false)
  const wasOffline = useRef(!online)

  useEffect(() => {
    if (!online) {
      wasOffline.current = true
      setBackOnline(false)
      announce('You’re offline.')
      return undefined
    }
    if (!wasOffline.current) return undefined
    wasOffline.current = false
    setBackOnline(true)
    announce('Back online.')
    const t = window.setTimeout(() => setBackOnline(false), 3000)
    return () => window.clearTimeout(t)
  }, [online])

  return (
    <>
      {!online ? (
        <StatusBar icon={WifiSlashIcon}>
          {pathname.startsWith('/paper/') ? 'You’re offline. Keep reading; questions will send when you’re back.' : (OFFLINE_COPY[pathname] ?? OFFLINE_COPY['/'])}
        </StatusBar>
      ) : backOnline ? (
        <StatusBar icon={CheckIcon} tone="success">
          Back online.
        </StatusBar>
      ) : null}
      {isAnthropicKeyMissing(health.data) ? <ApiKeyBar /> : null}
    </>
  )
}

function ApiKeyBar() {
  return (
    <StatusBar icon={WarningIcon} tone="warn" action={<CopyCommandButton />}>
      Answers are off: the server has no Anthropic API key. Set ANTHROPIC_API_KEY and restart it.
    </StatusBar>
  )
}

/** "Copy command" → "Copied" for 1.5 s. Shared by the bar and Settings → Connection. */
export function CopyCommandButton({ command = API_KEY_COMMAND }: { command?: string }) {
  const [copied, setCopied] = useState(false)
  useEffect(() => {
    if (!copied) return undefined
    const t = window.setTimeout(() => setCopied(false), 1500)
    return () => window.clearTimeout(t)
  }, [copied])
  return (
    <Button
      variant="text"
      tone="ink"
      textSize="small"
      onClick={() => {
        void copyText(command).then((ok) => {
          if (!ok) return
          setCopied(true)
          announce('Copied')
        })
      }}
    >
      <span className={s.reserve} data-label="Copy command">
        {copied ? 'Copied' : 'Copy command'}
      </span>
    </Button>
  )
}
