import { Component, type ErrorInfo, type ReactNode } from 'react'
import { useLocation } from 'react-router'
import { Button } from '../components/Button'
import p from './Page.module.css'
import s from './ErrorBoundary.module.css'

interface Props {
  children: ReactNode
  /** Changing this clears the error (the reader navigated somewhere else). */
  resetKey: string
}

interface State {
  error: Error | null
}

/**
 * Catches a render crash so one broken screen doesn't blank the whole app.
 * The fallback says what happened, that nothing on the server was lost, and
 * offers a reload; the technical detail is folded away for bug reports.
 */
class Boundary extends Component<Props, State> {
  override state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  override componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('PaperMind screen crashed', error, info.componentStack)
  }

  override componentDidUpdate(prev: Props) {
    if (prev.resetKey !== this.props.resetKey && this.state.error) this.setState({ error: null })
  }

  override render() {
    if (!this.state.error) return this.props.children
    return <CrashFallback error={this.state.error} />
  }
}

export function CrashFallback({ error }: { error: Error }) {
  return (
    <div className={p.page} role="alert">
      <div className={p.column}>
        <h1 className={p.title}>This screen stopped working.</h1>
        <p className={p.subtitle}>
          A bug in PaperMind stopped it partway. Your papers and what you know are saved on the server, so reloading is safe.
        </p>
        <div className={s.actions}>
          <Button variant="primary" onClick={() => window.location.reload()}>
            Reload
          </Button>
          <Button variant="secondary" onClick={() => window.location.assign('/')}>
            Back to your papers
          </Button>
        </div>
        <details className={s.details}>
          <summary className={s.summary}>Details for a bug report</summary>
          <pre className={s.pre}>{`${error.name}: ${error.message}`}</pre>
        </details>
      </div>
    </div>
  )
}

export function RouteErrorBoundary({ children }: { children: ReactNode }) {
  const { pathname } = useLocation()
  return <Boundary resetKey={pathname}>{children}</Boundary>
}
