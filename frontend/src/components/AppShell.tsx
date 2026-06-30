import type { ReactNode } from 'react'
import { TopBar } from './TopBar'

type AppShellProps = {
  children: ReactNode
  isRefreshing: boolean
  onRefresh: () => void
}

export function AppShell({ children, isRefreshing, onRefresh }: AppShellProps) {
  return (
    <div className="app-shell">
      <TopBar isRefreshing={isRefreshing} onRefresh={onRefresh} />
      <main className="app-shell__main">{children}</main>
    </div>
  )
}
