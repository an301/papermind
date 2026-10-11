import { CheckIcon, WarningCircleIcon, WarningIcon } from '@phosphor-icons/react'
import { useId, useState, type ReactNode } from 'react'
import { isAnthropicKeyMissing, useHealth, useUpdateUser, useUser } from '../../api/queries'
import type { Level } from '../../api/types'
import p from '../../app/Page.module.css'
import { openShortcuts } from '../../app/ShortcutsDialog'
import { CopyCommandButton } from '../../app/StatusBars'
import { Button } from '../../components/Button'
import { Code } from '../../components/Code'
import { SegmentedControl } from '../../components/SegmentedControl'
import { Switch } from '../../components/Switch'
import { TextLink } from '../../components/TextLink'
import { announce } from '../../lib/announce'
import { clsx } from '../../lib/clsx'
import { usePreferences, type AnswerSize, type PageMode, type ThemeChoice } from '../../lib/preferences'
import { LEVEL_OPTIONS, levelLabel } from '../onboarding/levels'
import { Onboarding } from '../onboarding/Onboarding'
import { ResetKnowledge } from './ResetKnowledge'
import s from './SettingsPage.module.css'

const THEMES = [
  { value: 'system', label: 'System' },
  { value: 'light', label: 'Light' },
  { value: 'dark', label: 'Dark' },
] as const satisfies readonly { value: ThemeChoice; label: string }[]

const PAGE_MODES = [
  { value: 'dimmed', label: 'Dimmed paper' },
  { value: 'night', label: 'Night page' },
  { value: 'inverted', label: 'Inverted' },
] as const satisfies readonly { value: PageMode; label: string }[]

const PAGE_MODE_NOTES: Record<PageMode, string> = {
  dimmed: 'The page stays paper, dimmed a little so it doesn’t glare.',
  night: 'Night page recolours the page. Figures turn two-tone.',
  inverted: 'For scanned papers. Colours in figures shift.',
}

const SIZES = [
  { value: 's', label: 'Small' },
  { value: 'm', label: 'Medium' },
  { value: 'l', label: 'Large' },
] as const satisfies readonly { value: AnswerSize; label: string }[]

const labelOf = <V extends string>(options: readonly { value: V; label: string }[], v: V) => options.find((o) => o.value === v)?.label ?? v

/**
 * Settings (DESIGN.md §4.7). Everything applies at once, with no Save button;
 * each change is announced politely ("Theme set to Dark").
 */
export function SettingsPage() {
  return (
    <div className={p.page}>
      <div className={p.narrow}>
        <h1 className={p.title}>Settings</h1>
        <Appearance />
        <Answers />
        <Keyboard />
        <WhatYouKnow />
        <Connection />
      </div>
    </div>
  )
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  const id = useId()
  return (
    <section className={s.section} aria-labelledby={id}>
      <h2 id={id} className={s.sectionTitle}>
        {title}
      </h2>
      <div className={s.rows}>{children}</div>
    </section>
  )
}

/** A label in the 200px column and the control beside it; stacked below 640px. */
function Row({ label, labelId, children, htmlFor }: { label: ReactNode; labelId?: string; htmlFor?: string; children: ReactNode }) {
  return (
    <div className={s.row}>
      {htmlFor ? (
        <label id={labelId} htmlFor={htmlFor} className={s.label}>
          {label}
        </label>
      ) : (
        <div id={labelId} className={s.label}>
          {label}
        </div>
      )}
      <div className={s.control}>{children}</div>
    </div>
  )
}

function Note({ children, id, className }: { children: ReactNode; id?: string; className?: string }) {
  return (
    <p id={id} className={clsx(s.note, className)}>
      {children}
    </p>
  )
}

function Appearance() {
  const theme = usePreferences((st) => st.theme)
  const pageMode = usePreferences((st) => st.pageMode)
  const answerSize = usePreferences((st) => st.answerSize)
  const set = usePreferences((st) => st.set)
  const ids = useId()
  return (
    <Section title="Appearance">
      <Row label="Theme" labelId={`${ids}-theme`}>
        <SegmentedControl
          aria-labelledby={`${ids}-theme`}
          options={THEMES}
          value={theme}
          onValueChange={(v) => {
            set('theme', v)
            announce(`Theme set to ${labelOf(THEMES, v)}`)
          }}
        />
      </Row>
      <Row label="Page in dark mode" labelId={`${ids}-page`}>
        <SegmentedControl
          aria-labelledby={`${ids}-page`}
          aria-describedby={`${ids}-page-note`}
          options={PAGE_MODES}
          value={pageMode}
          onValueChange={(v) => {
            set('pageMode', v)
            announce(`Page in dark mode set to ${labelOf(PAGE_MODES, v)}`)
          }}
        />
        <Note id={`${ids}-page-note`}>{PAGE_MODE_NOTES[pageMode]}</Note>
      </Row>
      <Row label="Answer text size" labelId={`${ids}-size`}>
        <SegmentedControl
          aria-labelledby={`${ids}-size`}
          options={SIZES}
          value={answerSize}
          onValueChange={(v) => {
            set('answerSize', v)
            announce(`Answer text size set to ${labelOf(SIZES, v)}`)
          }}
        />
        {/* The live preview: the one place outside answers where Literata appears (§2.3). */}
        <p className={s.preview} aria-hidden="true">
          We suspect that for large values of the key size, the dot products grow large in magnitude, pushing the softmax function into regions where it has extremely small gradients.
        </p>
      </Row>
    </Section>
  )
}

function Answers() {
  const user = useUser()
  const update = useUpdateUser()
  const labelBackground = usePreferences((st) => st.labelBackground)
  const set = usePreferences((st) => st.set)
  const ids = useId()
  const level: Level = user.data?.level ?? 'intermediate'
  const option = LEVEL_OPTIONS.find((o) => o.value === level) ?? LEVEL_OPTIONS[1]!
  return (
    <Section title="Answers">
      <Row label="Default depth" labelId={`${ids}-depth`}>
        <SegmentedControl
          aria-labelledby={`${ids}-depth`}
          aria-describedby={`${ids}-depth-note`}
          options={LEVEL_OPTIONS.map((o) => ({ value: o.value, label: o.label, tooltip: o.tooltip }))}
          segmentWidth={84}
          value={level}
          onValueChange={(v) => {
            update.mutate({ level: v })
            announce(`Default depth set to ${levelLabel(v)}. Applies to new conversations.`)
          }}
        />
        <Note id={`${ids}-depth-note`}>{option.description}</Note>
        {update.isError ? (
          <Note className={s.noteInk}>
            Couldn’t save the default depth. It’s back to {levelLabel(level)}.{' '}
            <Button variant="text" textSize="small" tone="ink" onClick={() => update.reset()}>
              Dismiss
            </Button>
          </Note>
        ) : null}
      </Row>
      <Row label="Label background paragraphs" labelId={`${ids}-bg`}>
        <div className={s.switchLine}>
          <Switch
            aria-labelledby={`${ids}-bg`}
            aria-describedby={`${ids}-bg-note`}
            checked={labelBackground}
            onCheckedChange={(on) => {
              set('labelBackground', on)
              announce(on ? 'Background labels on' : 'Background labels off')
            }}
          />
          <Note id={`${ids}-bg-note`} className={s.inlineNote}>
            Marks uncited paragraphs “Background”.
          </Note>
        </div>
      </Row>
    </Section>
  )
}

function Keyboard() {
  const on = usePreferences((st) => st.singleKeyShortcuts)
  const set = usePreferences((st) => st.set)
  const ids = useId()
  return (
    <Section title="Keyboard">
      <Row label="Single-key shortcuts" labelId={`${ids}-keys`}>
        <div className={s.switchLine}>
          <Switch
            aria-labelledby={`${ids}-keys`}
            aria-describedby={`${ids}-keys-note`}
            checked={on}
            onCheckedChange={(v) => {
              set('singleKeyShortcuts', v)
              announce(v ? 'Single-key shortcuts on' : 'Single-key shortcuts off')
            }}
          />
          <Note id={`${ids}-keys-note`} className={s.inlineNote}>
            E, A, H, 1–4, [, ], / and ? work outside text fields.
          </Note>
        </div>
      </Row>
      <div className={s.actionLine}>
        <Button variant="text" flush onClick={() => openShortcuts()}>
          See all shortcuts
        </Button>
      </div>
    </Section>
  )
}

function WhatYouKnow() {
  const [calibrating, setCalibrating] = useState(false)
  return (
    <Section title="What you know">
      <div className={s.actionStack}>
        <TextLink to="/know" className={s.plainLink}>
          Open What you know
        </TextLink>
        {calibrating ? (
          <div className={s.calibration}>
            <Onboarding
              context="inline"
              onDone={(outcome) => {
                setCalibrating(false)
                announce(outcome === 'done' ? 'Saved. Answers start where you chose.' : 'Starting questions closed.')
              }}
            />
          </div>
        ) : (
          <Button variant="text" flush className={s.start} onClick={() => setCalibrating(true)}>
            Go through the starting questions again
          </Button>
        )}
        <ResetKnowledge />
      </div>
    </Section>
  )
}

function Connection() {
  const health = useHealth()
  const ids = useId()
  const keyMissing = isAnthropicKeyMissing(health.data)
  return (
    <Section title="Connection">
      <Row label="Server" labelId={`${ids}-server`}>
        <p className={s.value} aria-live="off">
          {health.isPending ? (
            <span className={s.muted}>Checking…</span>
          ) : health.isError ? (
            <span className={s.withIcon}>
              <WarningCircleIcon size={16} aria-hidden="true" className={s.muted} />
              <span>
                Can’t reach the PaperMind server. If you’re running it yourself, start it with <Code>make dev</Code>.
              </span>
            </span>
          ) : (
            <span className="num">
              {health.data.status === 'ok' ? 'Connected' : 'Connected, with some services down'} · version {health.data.version}
            </span>
          )}
        </p>
        {health.data && health.data.status === 'degraded' ? <Degraded checks={health.data.checks} /> : null}
      </Row>
      <Row label="API key" labelId={`${ids}-key`}>
        {health.data ? (
          keyMissing ? (
            <div className={s.keyMissing}>
              <p className={s.withIcon}>
                <WarningIcon size={16} aria-hidden="true" className={s.warn} />
                <span>
                  <strong className={s.strong}>Missing.</strong> Set ANTHROPIC_API_KEY on the server and restart it.
                </span>
              </p>
              <CopyCommandButton />
            </div>
          ) : (
            <p className={clsx(s.value, s.withIcon)}>
              <CheckIcon size={16} aria-hidden="true" className={s.muted} />
              Set
            </p>
          )
        ) : (
          <p className={clsx(s.value, s.muted)}>{health.isError ? 'Unknown until the server answers' : 'Checking…'}</p>
        )}
      </Row>
    </Section>
  )
}

const SERVICE_NAMES: Record<string, string> = {
  redis: 'Sessions (Redis)',
  neo4j: 'Concept map (Neo4j)',
  'mcp:retriever': 'Paper search',
  'mcp:prereq_graph': 'Prerequisite checks',
  'mcp:semantic_scholar': 'Semantic Scholar',
}

/** Which services are down and what that means, in the server's own words. */
function Degraded({ checks }: { checks: Record<string, { ok: boolean; detail?: string | null }> }) {
  const down = Object.entries(checks).filter(([name, c]) => !c.ok && name !== 'anthropic_key')
  if (!down.length) return null
  return (
    <ul className={s.services}>
      {down.map(([name, c]) => (
        <li key={name} className={s.service}>
          <span className={s.serviceName}>{SERVICE_NAMES[name] ?? name}</span>
          {c.detail ? <span className={s.muted}>{c.detail}</span> : null}
        </li>
      ))}
    </ul>
  )
}
