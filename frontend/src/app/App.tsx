import { IconContext } from '@phosphor-icons/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { lazy, Suspense, type ReactNode } from 'react'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router'
import { queryClient } from '../api/queryClient'
import { LiveRegion } from '../components/LiveRegion'
import { Toaster, ToastProvider } from '../components/Toast'
import { TooltipProvider } from '../components/Tooltip'
import { KnowledgePage } from '../features/knowledge/KnowledgePage'
import { LibraryPage } from '../features/library/LibraryPage'
import { SettingsPage } from '../features/settings/SettingsPage'
import { AccessGate } from './AccessGate'
import { AppShell } from './AppShell'
import { RouteErrorBoundary } from './ErrorBoundary'
import { GlobalShortcuts } from './GlobalShortcuts'
import { NotFoundPage } from './NotFoundPage'
import { ShortcutsDialog } from './ShortcutsDialog'
import { IngestWatcher } from './IngestWatcher'

// The Reader carries pdf.js, KaTeX and the markdown stack; it loads only when a paper opens.
const ReaderRoute = lazy(() => import('../features/reader'))

const ICONS = { size: 16, weight: 'regular', mirrored: false } as const

/** Everything that wraps every screen. Exported for tests, which bring their own router. */
export function AppProviders({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider client={queryClient}>
      <IconContext.Provider value={ICONS}>
        <TooltipProvider>
          <ToastProvider>
            {children}
            <Toaster />
            <LiveRegion />
          </ToastProvider>
        </TooltipProvider>
      </IconContext.Provider>
    </QueryClientProvider>
  )
}

export function AppRoutes() {
  return (
    <RouteErrorBoundary>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<LibraryPage />} />
          <Route path="know" element={<KnowledgePage />} />
          <Route path="settings" element={<SettingsPage />} />
          <Route path="*" element={<NotFoundPage />} />
        </Route>
        {/* The task brief called the profile /knowledge; DESIGN.md §4.1 names it /know. Both work. */}
        <Route path="knowledge" element={<Navigate to="/know" replace />} />
        <Route
          path="paper/:paperId"
          element={
            <Suspense fallback={null}>
              <ReaderRoute />
            </Suspense>
          }
        />
      </Routes>
    </RouteErrorBoundary>
  )
}

export function App() {
  return (
    <AppProviders>
      <BrowserRouter>
        <AccessGate>
          <AppRoutes />
          <IngestWatcher />
          <GlobalShortcuts />
          <ShortcutsDialog />
        </AccessGate>
      </BrowserRouter>
    </AppProviders>
  )
}
