import { ButtonLink } from '../components/Button'
import p from './Page.module.css'
import s from './NotFoundPage.module.css'

/** Any address the app doesn't know. Says what happened and offers the way back. */
export function NotFoundPage() {
  return (
    <div className={p.page}>
      <div className={p.column}>
        <h1 className={p.title}>There’s no page at this address.</h1>
        <p className={p.subtitle}>The link may be out of date, or part of the address is missing.</p>
        <div className={s.actions}>
          <ButtonLink to="/" variant="primary">
            Back to your papers
          </ButtonLink>
        </div>
      </div>
    </div>
  )
}
