import { GearSixIcon } from '@phosphor-icons/react'
import { useEffect, useRef } from 'react'
import { Link, NavLink, Outlet, useLocation } from 'react-router'
import { IconLink } from '../components/IconButton'
import { StatusBars } from './StatusBars'
import s from './AppShell.module.css'

const TITLES: Record<string, string> = {
  '/': 'Your papers',
  '/know': 'What you know',
  '/settings': 'Settings',
}

/**
 * The frame around Library, What you know and Settings (DESIGN.md §4.2): a
 * 56px header whose wordmark shares its left edge with the page column, the
 * main nav, the Settings gear, and the app-level status bars. The Reader has
 * its own 48px top bar and doesn't use this.
 */
export function AppShell() {
  const { pathname } = useLocation()
  const headerRef = useRef<HTMLElement>(null)
  const mainRef = useRef<HTMLElement>(null)

  useEffect(() => {
    document.title = TITLES[pathname] ? `${TITLES[pathname]} · PaperMind` : 'PaperMind'
  }, [pathname])

  // F6 / Shift+F6 cycle the regions (§8.1). On these pages there are two: header and main.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'F6') return
      const regions = [headerRef.current, mainRef.current].filter((r): r is HTMLElement => !!r)
      const at = regions.findIndex((r) => r.contains(document.activeElement))
      const next = regions[(at + (e.shiftKey ? -1 : 1) + regions.length) % regions.length]
      if (!next) return
      e.preventDefault()
      next.focus()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  return (
    <div className={s.shell}>
      <a className={s.skip} href="#content">
        Skip to content
      </a>
      <header ref={headerRef} className={s.header} tabIndex={-1}>
        <div className={s.bar}>
          <Link to="/" className={s.wordmark}>
            PaperMind
          </Link>
          <MainNav className={s.navWide} />
          <IconLink
            to="/settings"
            label="Settings"
            icon={GearSixIcon}
            current={pathname === '/settings'}
            className={s.gear}
          />
        </div>
        <MainNav className={s.navNarrow} />
      </header>
      <StatusBars />
      <main ref={mainRef} id="content" className={s.main} tabIndex={-1}>
        <Outlet />
      </main>
    </div>
  )
}

function MainNav({ className }: { className?: string }) {
  return (
    <nav aria-label="Main" className={className}>
      <ul role="list" className={s.navList}>
        <li>
          <NavLink to="/" end className={s.navLink}>
            Your papers
          </NavLink>
        </li>
        <li aria-hidden="true" className={s.navDot}>
          ·
        </li>
        <li>
          <NavLink to="/know" className={s.navLink}>
            What you know
          </NavLink>
        </li>
      </ul>
    </nav>
  )
}
